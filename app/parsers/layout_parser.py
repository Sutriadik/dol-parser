"""
Open ADE — Layout-Aware Parser using PP-Structure (v2: Robust Multi-type Layout)

Menggunakan PaddlePaddle PP-Structure untuk mendeteksi region layout pada halaman dokumen:
- text / paragraph: Paragraf teks biasa
- title / section_header: Judul/heading → ## Heading
- list / list_item: Daftar bernomor / bullet points → 1. Item / - Item
- table: Tabel → diproses oleh SLANet → output HTML → konversi ke Markdown table
- figure / image: Jika ada baris teks/tabel OCR di dalamnya, diekstrak secara terstruktur.
- header / footer: Mempertahankan informasi nomor surat, tanggal, dan penandatangan.
"""

import io
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from app.logger import logger
from app.parsers.block_grouper import BARE_FORM_LABELS
from app.parsers.md_render import PenataList, block_to_markdown
from app.parsers.table_converter import html_table_to_markdown
from app.parsers.text_cleaner import clean_ocr_line, clean_ocr_text


@dataclass
class LayoutRegion:
    """Satu region layout yang terdeteksi oleh PP-Structure."""

    region_type: str  # text, title, table, figure, header, footer, list
    bbox: list[float]  # [x1, y1, x2, y2] in pixels
    text: str = ""  # Teks hasil OCR (untuk text/title/list regions)
    html: str = ""  # HTML table (untuk table regions)
    confidence: float = 0.0
    page: int = 1

    # Normalized bbox (0-1 range, relative to page dimensions)
    norm_xmin: float = 0.0
    norm_ymin: float = 0.0
    norm_xmax: float = 0.0
    norm_ymax: float = 0.0


def box_inside_regions(
    box: Any, regions: list[LayoutRegion], img_w: float, img_h: float, tol: float = 12.0
) -> bool:
    """
    True bila kotak OCR sudah tercakup region layout -- dicek di DUA sumbu.

    Versi sebelumnya hanya memeriksa tumpang-tindih vertikal, sehingga blok tanda tangan
    kanan yang sebaris dengan region kiri dianggap "sudah tertangkap" dan teksnya hilang.
    Koordinat kotak boleh ternormalisasi (0-1) atau piksel; bbox region selalu piksel.
    """
    b_xmin = box.xmin * img_w if box.xmin <= 1.0 else box.xmin
    b_xmax = box.xmax * img_w if box.xmax <= 1.0 else box.xmax
    b_ymin = box.ymin * img_h if box.ymin <= 1.0 else box.ymin
    b_ymax = box.ymax * img_h if box.ymax <= 1.0 else box.ymax
    box_h = max(b_ymax - b_ymin, 1.0)
    box_w = max(b_xmax - b_xmin, 1.0)

    for r in regions:
        rx1, ry1, rx2, ry2 = r.bbox
        overlap_y = max(0.0, min(b_ymax, ry2 + tol) - max(b_ymin, ry1 - tol))
        overlap_x = max(0.0, min(b_xmax, rx2 + tol) - max(b_xmin, rx1 - tol))
        if overlap_y / box_h > 0.5 and overlap_x / box_w > 0.5:
            return True
    return False


class LayoutParser:
    """
    Parser berbasis PP-Structure yang mendeteksi layout region,
    merekonstruksi tabel, dan menyusun markdown terstruktur.
    """

    _engine = None

    @classmethod
    def get_engine(cls):
        """Lazy-load PPStructure engine (singleton). Layout model PPStructure wajib en/ch."""
        if cls._engine is None:
            from paddleocr import PPStructure

            cls._engine = PPStructure(
                show_log=False,
                recovery=True,
                lang="en",  # Model layout PP-Structure hanya mendukung 'en' atau 'ch'
                use_gpu=False,
            )
            logger.info("🏗️  PP-Structure layout engine initialized (lang=en)")
        return cls._engine

    def _normalize_bbox(
        self, bbox: list[float], img_w: int, img_h: int
    ) -> tuple[float, float, float, float]:
        """Normalize pixel bbox ke 0-1 range."""
        x1, y1, x2, y2 = bbox
        return (
            round(max(0.0, min(1.0, x1 / img_w)), 5),
            round(max(0.0, min(1.0, y1 / img_h)), 5),
            round(max(0.0, min(1.0, x2 / img_w)), 5),
            round(max(0.0, min(1.0, y2 / img_h)), 5),
        )

    def _extract_boxes_and_text_from_res(self, res_list: Any) -> str:
        """
        Ekstrak teks terstruktur dari result list PP-Structure region.
        Menggunakan spatial line clustering jika ada beberapa baris OCR.
        """
        if not res_list:
            return ""

        if isinstance(res_list, str):
            return res_list.strip()

        from app.parsers.paddle_parser import OCRBox, cluster_and_merge_lines

        raw_boxes: list[OCRBox] = []
        if isinstance(res_list, list):
            for item in res_list:
                if isinstance(item, dict):
                    text = item.get("text", "").strip()
                    conf = float(item.get("confidence", 0.9))
                    tr = item.get("text_region")
                    if text and tr:
                        xs = [p[0] for p in tr]
                        ys = [p[1] for p in tr]
                        raw_boxes.append(
                            OCRBox(
                                text=text,
                                xmin=min(xs),
                                ymin=min(ys),
                                xmax=max(xs),
                                ymax=max(ys),
                                center_y=(min(ys) + max(ys)) / 2,
                                confidence=conf,
                            )
                        )
                    elif text:
                        raw_boxes.append(
                            OCRBox(
                                text=text,
                                xmin=0.0,
                                ymin=0.0,
                                xmax=1.0,
                                ymax=1.0,
                                center_y=0.0,
                                confidence=conf,
                            )
                        )
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    tr = item[0]
                    content = item[1]
                    text = ""
                    conf = 0.9
                    if isinstance(content, (list, tuple)):
                        text = str(content[0]).strip()
                        conf = float(content[1]) if len(content) > 1 else 0.9
                    elif isinstance(content, str):
                        text = content.strip()
                    if text and isinstance(tr, (list, tuple)) and len(tr) >= 4:
                        xs = [p[0] for p in tr]
                        ys = [p[1] for p in tr]
                        raw_boxes.append(
                            OCRBox(
                                text=text,
                                xmin=min(xs),
                                ymin=min(ys),
                                xmax=max(xs),
                                ymax=max(ys),
                                center_y=(min(ys) + max(ys)) / 2,
                                confidence=conf,
                            )
                        )
                    elif text:
                        raw_boxes.append(
                            OCRBox(
                                text=text,
                                xmin=0.0,
                                ymin=0.0,
                                xmax=1.0,
                                ymax=1.0,
                                center_y=0.0,
                                confidence=conf,
                            )
                        )

        if not raw_boxes:
            return ""

        from app.parsers.table_converter import format_structured_tabular_boxes

        structured_table = format_structured_tabular_boxes(raw_boxes, y_tol=14)
        if "|" in structured_table:
            return structured_table

        merged = cluster_and_merge_lines(raw_boxes, y_tolerance=12, x_gap_tolerance=80)
        lines_text = [b.text for b in merged if b.text.strip()]
        return "\n".join(lines_text)

    def _is_noise_region(self, region: LayoutRegion, img_h: int) -> bool:
        """Deteksi apakah region ini adalah noise murni (nomor halaman tunggal '1/1' atau whitespace
        kosong).
        """
        t = region.text.strip()
        if not t and not region.html:
            return True
        # Nomor halaman tunggal di pojok bawah misal "1 / 2"
        return bool(re.match(r"^\d+\s*/\s*\d+$", t) and region.bbox[1] / img_h > 0.92)

    def parse_page_layout(
        self,
        img_bytes: bytes,
        img_w: int,
        img_h: int,
        page_num: int,
        raw_ocr_boxes: list[Any] | None = None,
    ) -> list[LayoutRegion]:
        """
        Mendeteksi layout region pada satu halaman menggunakan PP-Structure,
        dan menggabungkan seluruh OCR boxes yang tidak tercover oleh layout detection
        sehingga tidak ada kop surat, nomor, perihal, lampiran, atau footer yang terlewat.

        Args:
            img_bytes: PNG bytes dari halaman PDF
            img_w: Lebar gambar (pixels)
            img_h: Tinggi gambar (pixels)
            page_num: Nomor halaman (1-indexed)
            raw_ocr_boxes: List of OCRBox dari PaddleOCR untuk 100% coverage

        Returns:
            List[LayoutRegion] yang sudah di-sort berdasarkan reading order
        """
        engine = self.get_engine()

        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_array = np.array(img)

        # Run PP-Structure
        result = engine(img_array)

        regions: list[LayoutRegion] = []

        for block in result:
            region_type = block.get("type", "text").lower()
            bbox = block.get("bbox", [0, 0, img_w, img_h])

            # Normalize bbox
            norm = self._normalize_bbox(bbox, img_w, img_h)

            region = LayoutRegion(
                region_type=region_type,
                bbox=bbox,
                confidence=block.get("score", 0.0),
                page=page_num,
                norm_xmin=norm[0],
                norm_ymin=norm[1],
                norm_xmax=norm[2],
                norm_ymax=norm[3],
            )

            if region_type == "table":
                res = block.get("res", {})
                if isinstance(res, dict):
                    region.html = res.get("html", "")
                elif isinstance(res, str):
                    region.html = res

                # Jika ada teks dari res, ekstrak juga
                if isinstance(res, list):
                    region.text = self._extract_boxes_and_text_from_res(res)
                elif not region.text:
                    region.text = "[TABLE]"

            elif region_type in ("figure", "image"):
                # Jangan buang teks OCR yang ada di dalam figure/tabel
                res = block.get("res", [])
                extracted_text = self._extract_boxes_and_text_from_res(res)
                if extracted_text and len(extracted_text) > 10:
                    region.text = extracted_text
                else:
                    region.text = "[IMAGE]"

            else:
                # text, title, list, header, footer, reference
                res = block.get("res", [])
                region.text = self._extract_boxes_and_text_from_res(res)

            # Filter noise murni
            if self._is_noise_region(region, img_h):
                continue

            regions.append(region)

        # Recover missed OCR boxes (letterheads, headers, footers, signatures)
        if raw_ocr_boxes:
            from app.parsers.paddle_parser import cluster_and_merge_lines

            missed = [b for b in raw_ocr_boxes if not box_inside_regions(b, regions, img_w, img_h)]
            if missed:
                # Group missed boxes into lines
                merged_missed = cluster_and_merge_lines(missed, y_tolerance=14, x_gap_tolerance=80)
                for mb in merged_missed:
                    mb_ymin = mb.ymin * img_h if mb.ymin <= 1.0 else mb.ymin
                    mb_ymax = mb.ymax * img_h if mb.ymax <= 1.0 else mb.ymax
                    mb_xmin = mb.xmin * img_w if mb.xmin <= 1.0 else mb.xmin
                    mb_xmax = mb.xmax * img_w if mb.xmax <= 1.0 else mb.xmax
                    mb_center_y = (mb_ymin + mb_ymax) / 2.0

                    if mb_center_y < img_h * 0.35:
                        r_type = "header"
                    elif mb_center_y > img_h * 0.70:
                        r_type = "footer"
                    else:
                        r_type = "text"

                    norm = self._normalize_bbox([mb_xmin, mb_ymin, mb_xmax, mb_ymax], img_w, img_h)
                    regions.append(
                        LayoutRegion(
                            region_type=r_type,
                            bbox=[mb_xmin, mb_ymin, mb_xmax, mb_ymax],
                            text=mb.text,
                            confidence=mb.confidence,
                            page=page_num,
                            norm_xmin=norm[0],
                            norm_ymin=norm[1],
                            norm_xmax=norm[2],
                            norm_ymax=norm[3],
                        )
                    )

        # Sort by reading order: top-to-bottom (Y), then left-to-right (X)
        # Dengan toleransi Y 20px agar item sebaris (misal Label : Nilai) diurutkan kiri->kanan
        regions.sort(key=lambda r: (round(r.bbox[1] / 20.0) * 20.0, r.bbox[0]))

        table_count = sum(1 for r in regions if r.region_type == "table")
        figure_count = sum(1 for r in regions if r.region_type in ("figure", "image"))
        logger.debug(
            f"  Page {page_num}: {len(regions)} layout regions detected "
            f"({table_count} tables, {figure_count} figures)"
        )

        return regions

    def regions_to_markdown(self, regions: list[LayoutRegion]) -> str:
        """Susun markdown terstruktur dari region PP-Structure (lihat _render_regions)."""
        return _render_regions(regions)


# Perender markdown untuk region PP-Structure. Label region tidak dipercaya mentah-mentah:
# PP-Structure bisa melabeli isi kontrak sebagai "footer"/"header". Label hanya dipakai
# bila posisinya juga cocok (footer asli di y >= 0,86); jenis baris ditentukan dari teksnya.

FOOTER_ZONE_Y = 0.85  # footer asli di dokumen uji mulai y=0,86
HEADER_ZONE_Y = 0.12  # kop surat; judul dokumen ("SURAT PERINTAH KERJA") ada di sini

# "Nomor : 687/..." / "Jabatan : Direktur". Label maksimal 4 kata, supaya kalimat seperti
# "Yang dalam hal ini mewakili secara sah : UNIVERSITAS TELKOM" tidak ikut jadi isian.
_KV = re.compile(r"^([A-Za-z][A-Za-z./ ]{0,40}?)\s*:\s*(\S.*)$")
# "3. WAKTU PELAKSANAAN" -- nomor + huruf kapital semua.
_NUMBERED_HEADING = re.compile(r"^\d{1,2}[.)]\s+[A-Z][A-Z0-9 &/,.\-]{2,}$")
# Awal butir daftar: "a)", "a.", "1.", "(1)", "-", dan "a PIHAK" (OCR sering membuang
# titik setelah huruf). Baris yang tidak diawali penanda ini adalah SAMBUNGAN butir
# sebelumnya, bukan butir baru -- versi lama menjadikan setiap baris yang terlipat
# sebagai bullet tersendiri ("- Tahun 2026 Kebutuhan Direktorat...").
_ITEM_START = re.compile(r"^(\(?[a-zA-Z0-9]{1,2}[.)]\s|[a-z]\s+(?=[A-Z])|[-*\u2022]\s)")
_COMPANY = re.compile(r"^(PT|CV)\b|PERSERO|\bTBK\b", re.IGNORECASE)


def _is_upper_heading(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return (
        len(line) <= 80
        and len(line.split()) >= 2
        and len(letters) >= 4
        and sum(c.isupper() for c in letters) / len(letters) > 0.9
        and not _COMPANY.search(line)
    )


def _classify_line(line: str) -> tuple[str, str]:
    """-> (tipe, teks) untuk satu baris di region teks biasa."""
    if _NUMBERED_HEADING.match(line):
        return "section_header", line
    m = _KV.match(line)
    if m and len(m.group(1).split()) <= 4:
        return "key_value", f"{m.group(1).strip()} : {m.group(2).strip()}"
    # Label tanpa titik dua ("Nama Budi Santoso Wijaya"): hanya untuk kata label yang
    # sudah dikenal dan baris pendek, supaya kalimat biasa yang kebetulan diawali "Lokasi"
    # tidak ikut terbaca sebagai isian.
    first, _, rest = line.partition(" ")
    if first.lower() in BARE_FORM_LABELS and rest and len(line) < 90:
        return "key_value", f"{first} : {rest.strip()}"
    return "paragraph", line


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def _render_regions(regions: list[LayoutRegion]) -> str:
    blocks: list[tuple[str, str]] = []
    emitted: set = set()  # butir yang sudah keluar di halaman ini
    last_heading = ""

    def emit(kind: str, text: str) -> None:
        nonlocal last_heading
        text = text.strip()
        if not text:
            return
        # Kolom "Alamat" sering terlipat ke baris berikut ("Kampus Universitas Telkom" /
        # "Jl. Telekomunikasi No. 1 ..."). Baris pendek tanpa titik dua tepat setelahnya
        # disambung ke nilai alamat, bukan jadi paragraf yatim.
        if (
            kind == "paragraph"
            and blocks
            and blocks[-1][0] == "key_value"
            and blocks[-1][1].lower().startswith("alamat")
            and len(text) < 80
            and ":" not in text
        ):
            blocks[-1] = ("key_value", f"{blocks[-1][1]}, {text}")
            return
        # Paragraf yang terpotong jadi beberapa region/baris disambung jadi satu.
        if (
            kind == "paragraph"
            and blocks
            and blocks[-1][0] == "paragraph"
            and not blocks[-1][1].rstrip().endswith((".", ":", ";"))
        ):
            blocks[-1] = ("paragraph", f"{blocks[-1][1]} {text}")
            return
        if kind == "section_header":
            if _norm(text) == last_heading:
                return
            last_heading = _norm(text)
        blocks.append((kind, text))

    for region in regions:
        text = (region.text or "").strip()
        r_type = region.region_type.lower()
        if not text and not region.html:
            continue

        if r_type == "table":
            if region.html:
                md_table = html_table_to_markdown(region.html)
                if md_table:
                    blocks.append(("table", md_table))
                    continue
            if text and text != "[TABLE]":
                blocks.append(("paragraph", text))
            continue

        if r_type in ("figure", "image"):
            blocks.append(("image", "" if text in ("", "[IMAGE]") else clean_ocr_line(text)))
            continue

        if r_type == "figure_caption":
            blocks.append(("caption", clean_ocr_line(text)))
            continue

        # "|" di luar region tabel hampir pasti garis vertikal kop surat/margin yang
        # terbaca OCR. Dibiarkan, clean_ocr_text menganggap barisnya baris tabel dan
        # mengosongkannya -- footer "... Indonesia |t0227566456 ..." sempat hilang total.
        lines = [re.sub(r"\s*\|\s*", " ", clean_ocr_line(b)).strip() for b in text.split("\n")]
        lines = [b for b in lines if b]
        if not lines:
            continue

        # Footer hanya kalau label DAN posisinya sama-sama bilang footer.
        if r_type == "footer" and region.norm_ymin >= FOOTER_ZONE_Y:
            blocks.append(("footer", " ".join(lines)))
            continue

        if r_type in ("title", "section_header"):
            joined = " ".join(lines)
            if _NUMBERED_HEADING.match(joined) or _is_upper_heading(joined):
                emit("section_header", joined)
            else:
                # PP-Structure juga memberi label "title" pada kalimat biasa
                # ("Garansi terlampir dalam SLA"); itu isi, bukan judul.
                emit("paragraph", joined)
            continue

        if r_type in ("list", "list_item"):
            items: list[str] = []
            for line in lines:
                if not items and _norm(line) == last_heading:
                    continue  # judul yang terulang di awal daftar
                if not items and (_NUMBERED_HEADING.match(line) or _is_upper_heading(line)):
                    emit("section_header", line)
                    continue
                if _ITEM_START.match(line) or not items:
                    items.append(line)
                else:
                    items[-1] = f"{items[-1]} {line}"
            for item in items:
                key = _norm(item)
                if key in emitted:  # region daftar yang saling tumpang tindih
                    continue
                emitted.add(key)
                blocks.append(("list_item", item))
            continue

        # text / paragraph / header / footer-yang-bukan-di-zona-footer
        in_header_zone = region.norm_ymax and region.norm_ymax <= HEADER_ZONE_Y
        for line in lines:
            if in_header_zone and _is_upper_heading(line):
                emit("section_header", line)  # judul dokumen di kop surat
                continue
            kind, out = _classify_line(line)
            emit(kind, out)

    penata_list = PenataList()
    md = "\n\n".join(
        block_to_markdown(kind, text, penata_list)
        for kind, text in blocks
        if text or kind == "image"
    )
    return clean_ocr_text(md)
