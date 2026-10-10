"""
Open ADE — Docling Parser (v4: native layout labels + picture detection + block grouping)

- PDF digital: Docling tanpa OCR (layout + TableFormer saja).
- PDF scan / mixed: Docling dengan OCR.
Converter dibuat lazy per mode, sehingga import docling/torch dan pemuatan model OCR
hanya terjadi saat benar-benar dibutuhkan (startup API tidak lagi memuat semua model).

Layout awareness (v4): Docling sudah menghasilkan label layout asli (section_header,
caption, list_item, dst.) dan deteksi gambar (doc.pictures) -- versi sebelumnya membuang
keduanya (label dikolapskan jadi header/paragraph, doc.pictures tidak pernah dibaca).
Sekarang label asli dipakai langsung, gambar/logo halaman pertama ikut jadi blok
tersendiri, dan baris-baris berdekatan yang senada digabung + blok tanda tangan
dideteksi via app.parsers.block_grouper (lihat modul itu untuk detail & alasan).
"""

import re
import time
import warnings
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.config import config
from app.logger import logger
from app.parsers.block_grouper import BARE_FORM_LABELS, DraftBlock, process_page_blocks
from app.parsers.md_render import PenataList, block_to_markdown, pasang_penanda_list
from app.parsers.text_cleaner import clean_ocr_text
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

# Keyakinan per blok tetap konstanta, TIDAK diganti skor Docling: nilai ini ikut mengali skor
# bukti (app/evidence/matcher.py), jadi mengubahnya menggeser ambang "bukti_kuat" untuk semua
# field tanpa pengukuran. Docling sendiri menyebut angka skornya informatif dan bisa berubah
# antar-versi. Laporan mutu Docling dibawa terpisah (lihat ringkas_mutu_pembacaan).
NATIVE_CONFIDENCE = 0.97
OCR_CONFIDENCE = 0.90
_NILAI_MUTU = ("poor", "fair", "good", "excellent")
LOGO_MAX_YMIN = 0.15  # gambar di 15% teratas halaman 1 dianggap logo/letterhead

# Label layout ASLI dari Docling (docling_core.types.doc.labels.DocItemLabel) -> tipe kanonis
# kita. Sebelumnya kita re-derive tipe dari teks (":" in text -> key_value, dst.) dan
# membuang informasi layout model yang sebenarnya sudah dihitung Docling secara gratis.
_DOCLING_LABEL_MAP: dict[str, str] = {
    "title": "title",
    "section_header": "section_header",
    "caption": "caption",
    "footnote": "footer",
    "page_header": "header",
    "page_footer": "footer",
    "list_item": "list_item",
    "picture": "image",
    "chart": "image",
    "formula": "paragraph",
    "code": "paragraph",
    "text": "paragraph",
    "paragraph": "paragraph",
}


# Kata label polos tanpa titik dua ("Nama" lalu ": Budi" sebagai item OCR terpisah) tidak
# tertangkap heuristik ":" dan bisa menyatu dengan paragraf narasi. Dikenali eksplisit sebagai
# key_value supaya tetap satu keluarga dengan nilainya.
def _classify_label(label: str, text_val: str) -> str:
    """Label asli Docling jadi acuan utama.

    Heuristik teks hanya menghaluskan label generik 'text'.
    """
    mapped = _DOCLING_LABEL_MAP.get(label, "paragraph")
    if mapped != "paragraph":
        return mapped
    if text_val.strip().lower().rstrip(":") in BARE_FORM_LABELS:
        return "key_value"
    if ":" in text_val and len(text_val) < 100:
        return "key_value"
    return mapped


@dataclass
class _OcrEngine:
    name: str
    options: object  # None = pakai default bawaan Docling


# Peta OCR_ENGINE -> (kelas options Docling, argumen, keterangan). OcrMac hanya ada di macOS.
# Dokumen kita berbahasa Indonesia dengan istilah Inggris (invoice, delivery, license), jadi
# Tesseract diberi dua bahasa sekaligus; tanpa "ind" kata berimbuhan banyak yang salah baca.
_TESSERACT_LANG = ["ind", "eng"]
_OCR_ENGINES = {
    "mac": ("OcrMacOptions", {}, "Apple Vision (hanya macOS)"),
    # EasyOCR dibuang 2026-10-07. Di benchmark 3 dokumen akurasinya
    # 47,3% vs RapidOCR 85,5%, dan RapidOCR sudah menutup kebutuhan lintas platform.
    # OCR_ENGINE=easyocr kini ditolak sebagai nilai tak dikenal.
    # Varian CLI memanggil biner `tesseract` langsung, jadi tidak perlu kompilasi tesserocr.
    "tesseract": (
        "TesseractCliOcrOptions",
        {"lang": _TESSERACT_LANG},
        "Tesseract CLI (butuh biner tesseract)",
    ),
    "tesserocr": (
        "TesseractOcrOptions",
        {"lang": _TESSERACT_LANG},
        "Tesseract via tesserocr (butuh kompilasi)",
    ),
    # RapidOCR dipaksa lang=["latin"]: default-nya ("chinese", sama dengan "en") memuat model
    # gabungan CJK+Latin yang buruk untuk teks Latin. Pada satu kontrak layanan penuh, rasio kata
    # rusak turun dari 18,3% ke 4,7% (Apple Vision 3,9%). Diukur pada 2 dokumen; RapidOCR hanya
    # punya model Latin ukuran mobile, jadi ini plafonnya.
    #
    # use_cls=False: pengklasifikasi arah membalik sebagian baris yang tegak lalu membuangnya
    # (kontrak scan 14 halaman: 66 baris hilang vs 3 baris noise tanpa cls). Harganya: halaman
    # yang di-scan terbalik 180 derajat tidak lagi tertolong.
    "rapidocr": (
        "RapidOcrOptions",
        {"lang": ["latin"], "use_cls": False},
        "RapidOCR (ONNX, lintas platform)",
    ),
}


def _resolve_ocr_engine(requested: str | None = None) -> _OcrEngine:
    """
    Tentukan engine OCR secara EKSPLISIT, dan katakan dengan keras yang mana.

    `requested` adalah argumen fungsi (paling diutamakan); kalau None, dipakai
    `config.OCR_ENGINE` (env var). Jadi pemanggil Python bisa menentukan engine per
    dokumen tanpa mengubah environment:

        DoclingParser().parse(pdf, ocr_engine="tesseract")

    Sebelumnya pemilihan engine dibungkus try/except tanpa nilai default: di macOS
    terpilih Apple Vision, di Linux exception-nya ditelan dan Docling diam-diam memakai
    engine lain. Hasil di laptop dan di server jadi berbeda tanpa ada yang tahu — persis
    jenis kesalahan yang baru ketahuan saat dokumen produksi salah terbaca.

    Nilai eksplisit (`mac`, `tesseract`, `rapidocr`) GAGAL TERANG-TERANGAN
    kalau engine itu tidak tersedia, bukan diam-diam mundur. `auto` memilihkan yang
    terbaik untuk platform ini dan mencatatnya di log.
    """
    import sys

    requested = (requested or config.OCR_ENGINE or "auto").strip().lower()

    def _load(key: str):
        class_name, kwargs, _desc = _OCR_ENGINES[key]
        from docling.datamodel import pipeline_options as po

        return getattr(po, class_name)(**kwargs)

    if requested == "auto":
        # macOS: Apple Vision jelas tercepat dan paling akurat di mesin ini.
        if sys.platform == "darwin":
            try:
                engine = _OcrEngine("mac", _load("mac"))
                logger.info("⚡ OCR: Apple Vision (OCR_ENGINE=auto di macOS)")
                return engine
            except Exception as e:
                logger.warning(f"⚠️  Apple Vision tidak tersedia walau di macOS: {e}")
        # Platform lain: serahkan ke default Docling, tapi katakan dengan jelas.
        logger.warning(
            f"⚠️  OCR: memakai engine DEFAULT Docling di platform '{sys.platform}'. "
            "Hasil bisa berbeda dengan macOS. Set OCR_ENGINE secara eksplisit "
            f"({'|'.join(_OCR_ENGINES)}) untuk produksi."
        )
        return _OcrEngine("docling-default", None)

    if requested not in _OCR_ENGINES:
        raise ValueError(
            f"OCR_ENGINE='{requested}' tidak dikenal. Pilihan: auto|{'|'.join(_OCR_ENGINES)}"
        )

    try:
        options = _load(requested)
    except Exception as e:
        # Sengaja dilempar, bukan di-fallback: diminta eksplisit berarti harus itu.
        raise RuntimeError(
            f"OCR_ENGINE='{requested}' ({_OCR_ENGINES[requested][2]}) diminta tapi tidak bisa "
            f"dimuat di platform ini: {e}"
        ) from e
    logger.info(f"⚡ OCR: {_OCR_ENGINES[requested][2]} (OCR_ENGINE={requested})")
    return _OcrEngine(requested, options)


# Aturan render dipindah ke app/parsers/md_render.py supaya jalur PaddleOCR memakai
# aturan yang sama persis. Nama lama dipertahankan untuk pemanggil di bawah.
_block_to_markdown = block_to_markdown


# Di bawah ini markdown sebuah halaman dianggap kehilangan teks. Bukan 1,0: pembersih OCR
# sengaja menghapus noise stempel/tanda tangan, dan itu bukan kehilangan.
AMBANG_CAKUPAN = 0.98


def _kata(teks: str) -> Counter:
    return Counter(re.findall(r"[0-9a-z]{3,}", clean_ocr_text(teks).lower()))


def cek_cakupan(
    sumber: dict[int, str], hasil: dict[int, str], ambang: float = AMBANG_CAKUPAN
) -> list[dict]:
    """Bandingkan teks mentah Docling per halaman dengan markdown akhirnya.

    Pengaman umum supaya kehilangan teks tidak lagi diam-diam: bug potongan tabel
    `[:3000]` sempat lama tidak ketahuan karena tidak ada yang membandingkan keduanya.
    """
    temuan = []
    for halaman, teks in sorted(sumber.items()):
        kata_sumber = _kata(teks)
        total = sum(kata_sumber.values())
        if not total:
            continue
        hilang = kata_sumber - _kata(hasil.get(halaman, ""))
        cakupan = 1 - sum(hilang.values()) / total
        if cakupan < ambang:
            temuan.append(
                {
                    "halaman": halaman,
                    "cakupan": round(cakupan, 3),
                    "contoh": [k for k, _ in hilang.most_common(10)],
                }
            )
    return temuan


def ringkas_mutu_pembacaan(laporan: Any) -> dict[str, Any] | None:
    """Laporan `confidence` Docling -> ringkasan untuk metadata; None bila tidak ada isinya.

    Yang dipegang nilai hurufnya (poor/fair/good/excellent), sesuai anjuran Docling; skor
    komponen ikut dibawa hanya sebagai keterangan. Skor yang tidak dihitung Docling (mis. OCR
    pada PDF digital, tabel di semua dokumen) bernilai NaN dan dikirim sebagai None.
    Tidak pernah melempar: laporan mutu tidak boleh menggagalkan pembacaan dokumen.
    """
    if laporan is None:
        return None

    def nilai(nama: str) -> str | None:
        v = getattr(laporan, nama, None)
        v = str(getattr(v, "value", v) or "").lower()
        return v if v in _NILAI_MUTU else None

    def skor(nama: str) -> float | None:
        try:
            v = float(getattr(laporan, nama, None))
        except (TypeError, ValueError):
            return None
        return round(v, 3) if v == v else None  # NaN tidak sama dengan dirinya sendiri

    try:
        # Rata-rata atas skor yang semuanya NaN membuat numpy memberi peringatan, bukan galat.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ringkasan = {
                "rata_rata": nilai("mean_grade"),
                "terendah": nilai("low_grade"),
                "skor_layout": skor("layout_score"),
                "skor_ocr": skor("ocr_score"),
                "skor_teks": skor("parse_score"),
            }
    except Exception as e:
        logger.warning(f"⚠️  Laporan mutu Docling tidak terbaca, dilewati: {e}")
        return None
    return ringkasan if ringkasan["rata_rata"] or ringkasan["terendah"] else None


def _blok_tabel(rows: list[list[str]], bbox, confidence: float) -> DraftBlock | None:
    """Satu tabel = satu blok, UTUH.

    Dulu teksnya dipotong `[:3000]`. Tabel markdown ber-padding cepat melewati batas itu:
    Lampiran I sebuah kontrak terpotong di tengah sel "6.2", sehingga baris 4-5,
    subtotal, total, dan termin hilang dari markdown maupun ekstraksi. Batas panjang
    prompt sudah diurus ekstraktor (EFFECTIVE_TEXT_MAX_CHARS), bukan di sini.
    """
    from app.parsers.table_converter import rows_to_markdown_table

    table_text = rows_to_markdown_table(rows, has_header=True)
    if not table_text:
        return None
    return DraftBlock(
        type="table", text=table_text, bbox=bbox, confidence=confidence, start=0, end=0
    )


class DoclingParser:
    def __init__(self) -> None:
        # Kunci cache memuat engine OCR juga: satu proses bisa memproses dokumen dengan
        # engine berbeda (mis. benchmark), dan converter untuk engine A tidak boleh
        # dipakai ulang untuk engine B.
        self._converters: dict[tuple, object] = {}

    def _get_converter(self, do_ocr: bool, ocr_engine: str | None = None):
        cache_key = (do_ocr, (ocr_engine or config.OCR_ENGINE or "auto").strip().lower())
        if cache_key not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            options = PdfPipelineOptions()
            options.do_ocr = do_ocr
            options.do_table_structure = True
            # Tanpa ini Docling memakai default-nya sendiri dan tidak memanfaatkan seluruh core.
            try:
                from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions

                options.accelerator_options = AcceleratorOptions(
                    num_threads=config.PARSER_NUM_THREADS,
                    device=AcceleratorDevice.AUTO,
                )
                logger.info(
                    f"⚡ Docling accelerator: {config.PARSER_NUM_THREADS} thread, device=AUTO"
                )
            except Exception as e:
                logger.info(f"ℹ️ AcceleratorOptions tidak tersedia, memakai default Docling: {e}")
            if do_ocr:
                options.images_scale = (
                    2.0  # Tingkatkan DPI render citra untuk OCR agar teks halus/miring terbaca
                )
                engine = _resolve_ocr_engine(ocr_engine)
                if engine.options is not None:
                    options.ocr_options = engine.options
            self._converters[cache_key] = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
            )
            logger.info(
                f"📄 Docling converter dimuat (OCR: {do_ocr}, Table Structure: True, Scale: "
                f"{2.0 if do_ocr else 1.0})"
            )
        return self._converters[cache_key]

    @staticmethod
    def _get_page_dimensions(doc, page_no: int) -> tuple:
        try:
            page = (getattr(doc, "pages", {}) or {}).get(page_no)
            size = getattr(page, "size", None)
            if size:
                return max(getattr(size, "width", 612), 1), max(getattr(size, "height", 792), 1)
        except Exception:
            pass
        return 612, 792

    @staticmethod
    def _normalize_bbox(bbox_obj, page_w: float, page_h: float) -> BoundingBox | None:
        """Docling memakai origin kiri-bawah; dikonversi ke origin kiri-atas ternormalisasi 0..1."""
        try:
            if hasattr(bbox_obj, "to_top_left_origin"):
                bbox_obj = bbox_obj.to_top_left_origin(page_height=page_h)
            left, t, r, b = bbox_obj.l, bbox_obj.t, bbox_obj.r, bbox_obj.b
        except Exception:
            return None
        xs = sorted(round(max(0.0, min(1.0, v / page_w)), 5) for v in (left, r))
        ys = sorted(round(max(0.0, min(1.0, v / page_h)), 5) for v in (t, b))
        return BoundingBox(xmin=xs[0], ymin=ys[0], xmax=xs[1], ymax=ys[1])

    def parse(
        self,
        pdf_path: str,
        do_ocr: bool = True,
        max_pages: int = None,
        ocr_engine: str | None = None,
    ) -> LandingAIParsedResponse:
        """`ocr_engine`: mac | rapidocr | tesseract | auto. None = pakai config."""
        start_time = time.time()
        converter = self._get_converter(do_ocr, ocr_engine)
        kwargs = {"page_range": (1, max_pages)} if max_pages else {}
        hasil = converter.convert(pdf_path, **kwargs)
        doc = hasil.document
        mutu = ringkas_mutu_pembacaan(getattr(hasil, "confidence", None))

        markdown_text = clean_ocr_text(doc.export_to_markdown())
        confidence = OCR_CONFIDENCE if do_ocr else NATIVE_CONFIDENCE
        page_dims: dict[int, tuple] = {}
        page_drafts: dict[int, list[DraftBlock]] = {}
        sumber_halaman: dict[int, list[str]] = {}  # teks mentah Docling, untuk cek_cakupan
        search_from = 0

        def locate(prov_list):
            if not prov_list:
                return 1, None
            prov = prov_list[0]
            page_no = getattr(prov, "page_no", 1)
            if page_no not in page_dims:
                page_dims[page_no] = self._get_page_dimensions(doc, page_no)
            bbox = getattr(prov, "bbox", None)
            box = self._normalize_bbox(bbox, *page_dims[page_no]) if bbox is not None else None
            return page_no, ((box.xmin, box.ymin, box.xmax, box.ymax) if box else None)

        # 1. Teks: label layout ASLI Docling dipakai langsung (lihat _classify_label).
        for item in doc.texts:
            text_val = (getattr(item, "text", "") or "").strip()
            if not text_val:
                continue
            page_no, bbox = locate(getattr(item, "prov", []))

            start_pos = markdown_text.find(text_val, max(0, search_from - 50))
            if start_pos == -1:
                start_pos, end_pos = (
                    search_from,
                    search_from,
                )  # teks berubah oleh cleaner: range kosong, bukan palsu
            else:
                end_pos = start_pos + len(text_val)
                search_from = end_pos

            sumber_halaman.setdefault(page_no, []).append(text_val)
            raw_label = str(getattr(item, "label", "text")).lower()
            elem_type = _classify_label(raw_label, text_val)
            if elem_type == "list_item":
                text_val = pasang_penanda_list(text_val, getattr(item, "marker", None))
            page_drafts.setdefault(page_no, []).append(
                DraftBlock(
                    type=elem_type,
                    text=text_val,
                    bbox=bbox,
                    confidence=confidence,
                    start=start_pos,
                    end=end_pos,
                )
            )

        # 2. Tabel (tidak ikut proses gabung -- satu tabel = satu blok, seperti sebelumnya).
        for table in doc.tables:
            page_no, bbox = locate(getattr(table, "prov", []))
            try:
                table_df = table.export_to_dataframe(doc=doc)
                rows = [[str(c) for c in table_df.columns]]
                rows += [[str(v) for v in row] for row in table_df.itertuples(index=False)]
            except Exception:
                continue
            sumber_halaman.setdefault(page_no, []).extend(c for row in rows[1:] for c in row)
            block = _blok_tabel(rows, bbox, confidence)
            if block:
                page_drafts.setdefault(page_no, []).append(block)

        # 3. Gambar/logo -- Docling sudah mendeteksinya (doc.pictures) tapi sebelumnya tidak
        #    pernah dibaca sama sekali. Logo/letterhead biasanya di bagian atas halaman 1.
        for picture in getattr(doc, "pictures", []):
            page_no, bbox = locate(getattr(picture, "prov", []))
            is_logo = page_no == 1 and bbox is not None and bbox[1] <= LOGO_MAX_YMIN
            page_drafts.setdefault(page_no, []).append(
                DraftBlock(
                    type="logo" if is_logo else "image",
                    text="",
                    bbox=bbox,
                    confidence=confidence,
                    start=0,
                    end=0,
                )
            )

        # 4. Gabung baris senada berdekatan, tandai blok attestation (tanda tangan), dan
        #    urutkan ala urutan baca per halaman.
        pages_structure = []
        full_markdown = ""
        penata_list = PenataList()  # tingkat list berlanjut antar-halaman
        md_halaman: dict[int, str] = {}

        for p_no in sorted(page_drafts):
            processed = process_page_blocks(page_drafts[p_no])
            page_block_strings = []

            for draft in processed:
                if not draft.text or not draft.text.strip():
                    continue
                text_str = draft.text.strip()
                block_md = _block_to_markdown(draft.type, text_str, penata_list)
                page_block_strings.append((draft, block_md))

            page_md = "\n\n".join(md for _, md in page_block_strings)
            md_halaman[p_no] = page_md
            page_start_offset = len(full_markdown) + (
                len("\n\n<!-- PAGE BREAK -->\n\n") if full_markdown else 0
            )

            curr_offset = page_start_offset
            for draft, block_md in page_block_strings:
                draft.start = curr_offset
                draft.end = curr_offset + len(block_md)
                curr_offset += len(block_md) + 2

            if full_markdown:
                full_markdown += "\n\n<!-- PAGE BREAK -->\n\n" + page_md
            else:
                full_markdown = page_md

            children: list[StructureItem] = []
            for idx, draft in enumerate(processed):
                box = (
                    BoundingBox(
                        xmin=draft.bbox[0],
                        ymin=draft.bbox[1],
                        xmax=draft.bbox[2],
                        ymax=draft.bbox[3],
                    )
                    if draft.bbox
                    else full_page_bbox()
                )
                grounding = Grounding(
                    page=p_no,
                    range=TextRange(start=draft.start or 0, end=draft.end or 0),
                    box=box,
                    confidence=draft.confidence,
                )
                atomic = []
                for sub in draft.atomic or [draft]:
                    sub_box = (
                        BoundingBox(
                            xmin=sub.bbox[0], ymin=sub.bbox[1], xmax=sub.bbox[2], ymax=sub.bbox[3]
                        )
                        if sub.bbox
                        else box
                    )
                    atomic.append(
                        AtomicGrounding(
                            page=p_no,
                            range=TextRange(start=sub.start or 0, end=sub.end or 0),
                            box=sub_box,
                            text=sub.text or None,
                            confidence=sub.confidence,
                        )
                    )
                children.append(
                    StructureItem(
                        type=draft.type,
                        id=f"{draft.type}-p{p_no}-{idx}",
                        text=draft.text or None,
                        grounding=grounding,
                        confidence=draft.confidence,
                        atomic_grounding=atomic,
                    )
                )

            pages_structure.append(
                StructureItem(
                    type="page",
                    id=f"page-{p_no}",
                    grounding=Grounding(
                        page=p_no,
                        range=TextRange(
                            start=page_start_offset, end=page_start_offset + len(page_md)
                        ),
                        box=full_page_bbox(),
                        confidence=1.0,
                    ),
                    children=children,
                    confidence=1.0,
                )
            )

        markdown_text = (
            clean_ocr_text(full_markdown)
            if full_markdown
            else clean_ocr_text(doc.export_to_markdown())
        )

        teks_hilang = cek_cakupan({h: "\n".join(t) for h, t in sumber_halaman.items()}, md_halaman)
        for t in teks_hilang:
            logger.warning(
                f"⚠️  Halaman {t['halaman']}: hanya {t['cakupan']:.0%} teks Docling sampai ke "
                f"markdown; contoh yang hilang: {', '.join(t['contoh'])}"
            )

        duration_ms = int((time.time() - start_time) * 1000)
        engine_name = "ibm-docling" + ("+ocr" if do_ocr else "")
        block_count = sum(len(p.children) for p in pages_structure)
        logger.info(
            f"✅ Docling selesai [{engine_name}]: {len(pages_structure)} halaman, "
            f"{block_count} blok (setelah pengelompokan), {len(markdown_text)} karakter, "
            f"{duration_ms}ms"
        )
        if mutu:
            logger.info(
                f"🔎 Mutu pembacaan Docling: rata-rata {mutu['rata_rata']}, "
                f"terendah {mutu['terendah']}"
            )
        return LandingAIParsedResponse(
            markdown=markdown_text,
            metadata=ParseMetadata(
                job_id=f"parse-docling-{int(time.time())}",
                page_count=len(pages_structure) or 1,
                output_markdown_chars=len(markdown_text),
                duration_ms=duration_ms,
                is_scanned=do_ocr,
                parser_engine=engine_name,
                teks_hilang=teks_hilang,
                mutu_pembacaan=mutu,
            ),
            structure=DocumentStructure(children=pages_structure),
        )
