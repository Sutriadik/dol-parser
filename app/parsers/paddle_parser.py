"""
Open ADE — PaddleOCR Parser (v2: Layout-Aware)

Dua mode:
1. LAYOUT MODE (default): Menggunakan PP-Structure untuk deteksi region layout
   (text, table, title, figure) + SLANet table recognition → markdown terstruktur.
2. FALLBACK MODE: Jika PPStructure tidak tersedia, gunakan PaddleOCR + spatial
   clustering (mode lama).

Kedua mode menghasilkan output yang sama: LandingAIParsedResponse.
"""

import re
import time
from dataclasses import dataclass

import pymupdf as fitz

from app.config import config
from app.logger import logger
from app.parsers.image_enhancer import (
    pixmap_to_bgr,
    preprocess_image_for_ocr,
    render_pdf_page_high_res,
)
from app.parsers.text_cleaner import clean_ocr_line, clean_ocr_text
from app.schemas.common import (
    AtomicGrounding,
    BoundingBox,
    DocumentStructure,
    Grounding,
    LandingAIParsedResponse,
    ParseMetadata,
    StructureItem,
    TextRange,
    full_page_bbox,
)

MIN_TEXT_CHARS_PER_PAGE = 50

# OCR Box and Spatial Clustering (Fallback mode)


@dataclass
class OCRBox:
    text: str
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    center_y: float
    confidence: float


def cluster_and_merge_lines(
    boxes: list[OCRBox], y_tolerance: float = None, x_gap_tolerance: float = None
) -> list[OCRBox]:
    """
    Mengelompokkan dan menggabungkan potongan teks OCR yang berada pada baris
    horizontal yang sama (Spatial Reading Order).
    """
    if y_tolerance is None:
        y_tolerance = config.OCR_Y_TOLERANCE
    if x_gap_tolerance is None:
        x_gap_tolerance = config.OCR_X_GAP_TOLERANCE

    if not boxes:
        return []

    sorted_boxes = sorted(boxes, key=lambda b: (round(b.ymin / y_tolerance) * y_tolerance, b.xmin))
    merged_lines: list[OCRBox] = []
    current_line: list[OCRBox] = [sorted_boxes[0]]

    for box in sorted_boxes[1:]:
        last = current_line[-1]
        same_row = abs(box.center_y - last.center_y) <= y_tolerance or (
            box.ymin >= last.ymin - y_tolerance and box.ymax <= last.ymax + y_tolerance
        )
        horizontal_gap = box.xmin - last.xmax

        if same_row and horizontal_gap <= x_gap_tolerance:
            current_line.append(box)
        else:
            current_line.sort(key=lambda b: b.xmin)
            merged_text = " ".join(b.text for b in current_line)
            merged_text = re.sub(r"\s*:\s*", " : ", merged_text)
            merged_text = re.sub(r"\s+", " ", merged_text).strip()
            merged_text = clean_ocr_line(merged_text)

            merged_lines.append(
                OCRBox(
                    text=merged_text,
                    xmin=min(b.xmin for b in current_line),
                    ymin=min(b.ymin for b in current_line),
                    xmax=max(b.xmax for b in current_line),
                    ymax=max(b.ymax for b in current_line),
                    center_y=sum(b.center_y for b in current_line) / len(current_line),
                    confidence=sum(b.confidence for b in current_line) / len(current_line),
                )
            )
            current_line = [box]

    if current_line:
        current_line.sort(key=lambda b: b.xmin)
        merged_text = re.sub(r"\s*:\s*", " : ", " ".join(b.text for b in current_line))
        merged_text = re.sub(r"\s+", " ", merged_text).strip()
        merged_text = clean_ocr_line(merged_text)

        merged_lines.append(
            OCRBox(
                text=merged_text,
                xmin=min(b.xmin for b in current_line),
                ymin=min(b.ymin for b in current_line),
                xmax=max(b.xmax for b in current_line),
                ymax=max(b.ymax for b in current_line),
                center_y=sum(b.center_y for b in current_line) / len(current_line),
                confidence=sum(b.confidence for b in current_line) / len(current_line),
            )
        )

    return merged_lines


# PaddleOCR Parser Class


class PaddleOCRParser:
    _ocr_engine = None
    _layout_parser = None

    @classmethod
    def get_engine(cls):
        if cls._ocr_engine is None:
            # Import lazy: paddle (~430MB) hanya dimuat jika engine Paddle benar-benar dipakai.
            from paddleocr import PaddleOCR

            cls._ocr_engine = PaddleOCR(use_angle_cls=True, lang=config.OCR_LANG)
        return cls._ocr_engine

    @classmethod
    def get_layout_parser(cls):
        """Lazy-load LayoutParser (PPStructure). Returns None jika tidak tersedia."""
        if cls._layout_parser is None and config.ENABLE_LAYOUT_ANALYSIS:
            try:
                from app.parsers.layout_parser import LayoutParser

                cls._layout_parser = LayoutParser()
                logger.info("Layout parser (PPStructure) loaded")
            except Exception as e:
                logger.warning(f"Layout parser tidak tersedia, fallback ke spatial clustering: {e}")
                cls._layout_parser = False  # False = tried and failed
        return cls._layout_parser if cls._layout_parser is not False else None

    def _classify_element_type(self, text_content: str) -> str:
        """Deteksi tipe elemen layout berdasarkan konten teks."""
        text_upper = text_content.upper().strip()

        header_keywords = [
            "SURAT PERINTAH KERJA",
            "SURAT PENAWARAN",
            "BERITA ACARA",
            "PERJANJIAN KERJASAMA",
            "LAMPIRAN",
            "KONTRAK PENGADAAN",
        ]
        if any(h in text_upper for h in header_keywords):
            return "header"

        if re.match(r"^\d+[\.)\]]\s*[A-Z\s]{3,}", text_content):
            return "header"

        if ":" in text_content and len(text_content) < 120:
            return "key_value"

        table_keywords = [
            "uraian barang",
            "harga satuan",
            "jumlah harga",
            "sub total",
            "ppn 11%",
            "ppn11%",
        ]
        if "|" in text_content or any(col in text_content.lower() for col in table_keywords):
            return "table"

        signature_keywords = ["METERAI TEMPEL", "METERAI", "TANDA TANGAN"]
        if len(text_content) < 60 and any(s in text_upper for s in signature_keywords):
            return "signature"

        return "paragraph"

    def _is_footer_noise(self, text_content: str) -> bool:
        """Deteksi noise murni (nomor halaman tunggal atau URL murni tanpa teks lain)."""
        t = text_content.strip()
        if not t:
            return True
        if re.match(r"^\d+\s*/\s*\d+$", t):
            return True
        return bool(re.match(r"^(https?://)?www\.[a-zA-Z0-9\.\-_]+\.[a-zA-Z]{2,4}/?$", t))

    # LAYOUT MODE — PP-Structure

    def _parse_with_layout(self, pdf_path: str, max_pages: int = None) -> LandingAIParsedResponse:
        """
        Layout-aware parsing menggunakan PP-Structure.
        Mendeteksi region (text/table/title/figure) dan merekonstruksi markdown terstruktur.
        """
        start_time = time.time()
        layout_parser = self.get_layout_parser()
        doc = fitz.open(pdf_path)

        pages_structure = []
        full_markdown_parts = []
        char_offset = 0

        total_pages = len(doc)
        pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

        logger.info(f"PP-Structure Layout Mode: Memproses {pages_to_process}/{total_pages} halaman")

        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            page_num = page_idx + 1

            # SATU render per halaman, dipakai untuk dua kebutuhan sekaligus: PNG untuk
            # PP-Structure dan array BGR untuk OCR. Dulu halaman yang sama dirender DUA KALI
            # pada DPI yang sama (get_pixmap + render_pdf_page_high_res) -- murni kerja ganda,
            # hasilnya byte-identical.
            pix = page.get_pixmap(dpi=config.DEFAULT_DPI)
            img_bytes = pix.tobytes("png")
            img_w = pix.width
            img_h = pix.height

            # Extract raw OCR boxes for complete 100% page coverage
            enhanced_bgr = preprocess_image_for_ocr(pixmap_to_bgr(pix))
            ocr_results = self.get_engine().ocr(enhanced_bgr, cls=True)
            raw_boxes: list[OCRBox] = []
            if ocr_results and ocr_results[0]:
                for line in ocr_results[0]:
                    box_coords = line[0]
                    text_content = line[1][0].strip()
                    confidence = round(float(line[1][1]), 3)
                    if not text_content:
                        continue
                    xs = [pt[0] for pt in box_coords]
                    ys = [pt[1] for pt in box_coords]
                    raw_boxes.append(
                        OCRBox(
                            text=text_content,
                            xmin=min(xs),
                            ymin=min(ys),
                            xmax=max(xs),
                            ymax=max(ys),
                            center_y=(min(ys) + max(ys)) / 2,
                            confidence=confidence,
                        )
                    )

            # Run PP-Structure layout detection with 100% OCR box recovery
            regions = layout_parser.parse_page_layout(
                img_bytes, img_w, img_h, page_num, raw_ocr_boxes=raw_boxes
            )

            # Convert regions to markdown
            page_md = layout_parser.regions_to_markdown(regions)

            # Build structure items from regions
            page_items: list[StructureItem] = []
            for idx, region in enumerate(regions):
                start_char = char_offset
                end_char = start_char + len(region.text)

                bbox = BoundingBox(
                    xmin=min(region.norm_xmin, region.norm_xmax),
                    ymin=min(region.norm_ymin, region.norm_ymax),
                    xmax=max(region.norm_xmin, region.norm_xmax),
                    ymax=max(region.norm_ymin, region.norm_ymax),
                )

                st_item = StructureItem(
                    type=region.region_type,
                    id=f"{region.region_type}-p{page_num}-{idx}",
                    text=region.text,
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=start_char, end=end_char),
                        box=bbox,
                        confidence=round(region.confidence, 3),
                    ),
                    atomic_grounding=[
                        AtomicGrounding(
                            page=page_num,
                            range=TextRange(start=start_char, end=end_char),
                            box=bbox,
                            text=region.text,
                            confidence=round(region.confidence, 3),
                        )
                    ],
                    confidence=round(region.confidence, 3),
                )
                page_items.append(st_item)

            full_markdown_parts.append(page_md)
            char_offset += len(page_md) + 20

            pages_structure.append(
                StructureItem(
                    type="page",
                    id=f"page-{page_num}",
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=0, end=len(page_md)),
                        box=full_page_bbox(),
                        confidence=1.0,
                    ),
                    children=page_items,
                    confidence=1.0,
                )
            )

            # Log region summary
            table_count = sum(1 for r in regions if r.region_type == "table")
            figure_count = sum(1 for r in regions if r.region_type == "figure")
            logger.info(
                f"  Page {page_num}: {len(regions)} regions "
                f"({table_count} tables, {figure_count} figures)"
            )

        doc.close()
        raw_markdown = "\n\n<!-- PAGE BREAK -->\n\n".join(full_markdown_parts)
        cleaned_markdown = clean_ocr_text(raw_markdown)
        duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"PP-Structure selesai: {len(pages_structure)} halaman, {len(cleaned_markdown)} "
            f"karakter, {duration_ms}ms"
        )

        return LandingAIParsedResponse(
            markdown=cleaned_markdown,
            metadata=ParseMetadata(
                job_id=f"parse-ppstructure-{int(time.time())}",
                page_count=len(pages_structure),
                output_markdown_chars=len(cleaned_markdown),
                duration_ms=duration_ms,
                is_scanned=True,
                parser_engine="ppstructure+slanet",
            ),
            structure=DocumentStructure(children=pages_structure),
        )

    # FALLBACK MODE — PaddleOCR + Spatial Clustering

    def _parse_with_ocr(self, pdf_path: str, max_pages: int = None) -> LandingAIParsedResponse:
        """
        Fallback: PaddleOCR + spatial clustering tanpa layout analysis.
        Digunakan ketika PP-Structure tidak tersedia.
        """
        start_time = time.time()
        ocr = self.get_engine()
        doc = fitz.open(pdf_path)

        pages_structure = []
        full_markdown_parts = []
        char_offset = 0

        total_pages = len(doc)
        pages_to_process = min(total_pages, max_pages) if max_pages else total_pages

        logger.info(f"PaddleOCR Fallback Mode: Memproses {pages_to_process}/{total_pages} halaman")

        for page_idx in range(pages_to_process):
            page = doc[page_idx]
            page_num = page_idx + 1

            high_res_bgr = render_pdf_page_high_res(page, target_dpi=config.DEFAULT_DPI)
            enhanced_bgr = preprocess_image_for_ocr(high_res_bgr)
            img_h, img_w = enhanced_bgr.shape[:2]

            ocr_results = ocr.ocr(enhanced_bgr, cls=True)
            raw_boxes: list[OCRBox] = []

            if ocr_results and ocr_results[0]:
                for line in ocr_results[0]:
                    box_coords = line[0]
                    text_content = line[1][0].strip()
                    confidence = round(float(line[1][1]), 3)

                    if not text_content:
                        continue

                    xs = [pt[0] for pt in box_coords]
                    ys = [pt[1] for pt in box_coords]
                    xmin = round(max(0.0, min(1.0, min(xs) / img_w)), 5)
                    ymin = round(max(0.0, min(1.0, min(ys) / img_h)), 5)
                    xmax = round(max(0.0, min(1.0, max(xs) / img_w)), 5)
                    ymax = round(max(0.0, min(1.0, max(ys) / img_h)), 5)

                    raw_boxes.append(
                        OCRBox(
                            text=text_content,
                            xmin=xmin,
                            ymin=ymin,
                            xmax=xmax,
                            ymax=ymax,
                            center_y=(ymin + ymax) / 2,
                            confidence=confidence,
                        )
                    )

            merged_lines = cluster_and_merge_lines(raw_boxes)

            page_items: list[StructureItem] = []
            page_md_lines = []

            for item_idx, line in enumerate(merged_lines):
                text_content = line.text

                if self._is_footer_noise(text_content):
                    continue

                start_char = char_offset + sum(len(b) + 1 for b in page_md_lines)
                end_char = start_char + len(text_content)
                elem_type = self._classify_element_type(text_content)

                if elem_type == "header":
                    page_md_lines.append(f"\n## {text_content}")
                elif elem_type == "signature":
                    page_md_lines.append(f"[SIGNED] {text_content}")
                else:
                    page_md_lines.append(text_content)

                bbox = BoundingBox(xmin=line.xmin, ymin=line.ymin, xmax=line.xmax, ymax=line.ymax)
                st_item = StructureItem(
                    type=elem_type,
                    id=f"{elem_type}-p{page_num}-{item_idx}",
                    text=text_content,
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=start_char, end=end_char),
                        box=bbox,
                        confidence=round(line.confidence, 3),
                    ),
                    atomic_grounding=[
                        AtomicGrounding(
                            page=page_num,
                            range=TextRange(start=start_char, end=end_char),
                            box=bbox,
                            text=text_content,
                            confidence=round(line.confidence, 3),
                        )
                    ],
                    confidence=round(line.confidence, 3),
                )
                page_items.append(st_item)

            page_md = "\n\n".join(page_md_lines)
            full_markdown_parts.append(page_md)
            char_offset += len(page_md) + 20

            pages_structure.append(
                StructureItem(
                    type="page",
                    id=f"page-{page_num}",
                    grounding=Grounding(
                        page=page_num,
                        range=TextRange(start=0, end=len(page_md)),
                        box=full_page_bbox(),
                        confidence=1.0,
                    ),
                    children=page_items,
                    confidence=1.0,
                )
            )

        doc.close()
        raw_markdown = "\n\n<!-- PAGE BREAK -->\n\n".join(full_markdown_parts)
        cleaned_markdown = clean_ocr_text(raw_markdown)
        duration_ms = int((time.time() - start_time) * 1000)

        logger.info(
            f"PaddleOCR selesai: {len(pages_structure)} halaman, {len(cleaned_markdown)} "
            f"karakter, {duration_ms}ms"
        )

        return LandingAIParsedResponse(
            markdown=cleaned_markdown,
            metadata=ParseMetadata(
                job_id=f"parse-paddle-{int(time.time())}",
                page_count=len(pages_structure),
                output_markdown_chars=len(cleaned_markdown),
                duration_ms=duration_ms,
                is_scanned=True,
                parser_engine="paddleocr+spatial_cluster",
            ),
            structure=DocumentStructure(children=pages_structure),
        )

    # Public API

    def parse(self, pdf_path: str, max_pages: int = None) -> LandingAIParsedResponse:
        """
        Memproses dokumen scan menggunakan strategi terbaik yang tersedia:
        1. PP-Structure (layout + table) jika tersedia dan enabled
        2. PaddleOCR + spatial clustering sebagai fallback
        """
        layout_parser = self.get_layout_parser()

        if layout_parser is not None:
            try:
                result = self._parse_with_layout(pdf_path, max_pages)
            except Exception as e:
                logger.warning(f"PP-Structure gagal, fallback ke PaddleOCR: {e}")
                return self._parse_with_ocr(pdf_path, max_pages)
            # Halaman scan penuh sering dideteksi sebagai satu region "figure" sehingga teksnya
            # tidak pernah di-OCR (hasil hanya penanda [IMAGE]). Jika begitu, OCR ulang tanpa
            # layout.
            real_chars = len(re.sub(r"\[IMAGE[^\]]*\]|\s+", "", result.markdown))
            if real_chars < MIN_TEXT_CHARS_PER_PAGE * max(result.metadata.page_count, 1):
                logger.warning(
                    f"PP-Structure hanya menghasilkan {real_chars} karakter teks "
                    f"({result.metadata.page_count} hlm) → OCR ulang tanpa layout analysis"
                )
                return self._parse_with_ocr(pdf_path, max_pages)
            return result
        else:
            return self._parse_with_ocr(pdf_path, max_pages)
