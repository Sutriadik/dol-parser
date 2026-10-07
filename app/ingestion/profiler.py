"""
Open ADE — Document profiler (Plan §7.1).

Menentukan karakteristik dokumen per halaman SEBELUM memilih engine, memakai PyMuPDF
saja (cepat, tanpa model). Hasilnya dipakai router untuk memutuskan apakah OCR perlu.

Sebelumnya Docling selalu dijalankan dengan do_ocr=True, termasuk untuk PDF digital:
model OCR tetap dimuat dan menjalankan OCR pada setiap gambar/logo halaman tanpa manfaat.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import config


@dataclass
class PageProfile:
    page_number: int
    native_chars: int
    image_coverage: float
    needs_ocr: bool


@dataclass
class DocumentProfile:
    file_name: str
    page_count: int
    kind: str  # native | scanned | mixed
    native_text_ratio: float
    scanned_page_ratio: float
    ocr_pages: list[int] = field(default_factory=list)
    pages: list[PageProfile] = field(default_factory=list)
    # >0 kalau separuh akhir dokumen adalah duplikat persis separuh awal (mis. PDF yang
    # sama ter-gabung dua kali saat diunggah) -- nilainya jumlah halaman ASLI (separuh).
    # Ditemukan dari PKS 14 halaman yang isinya 7 halaman diulang persis dua kali,
    # menyebabkan teks 2x lebih besar dikirim ke LLM tanpa manfaat apa pun.
    duplicate_block_pages: int = 0

    @property
    def needs_ocr(self) -> bool:
        return bool(self.ocr_pages)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pages"] = data["pages"][:50]  # batasi ukuran output untuk dokumen tebal
        return data


def _detect_duplicate_block(pages: list[PageProfile], text_hashes: list[int]) -> int:
    """
    0 kecuali separuh akhir dokumen adalah duplikat PERSIS separuh awal, halaman demi
    halaman. Cuma dipercaya kalau halaman-halaman itu punya teks native memadai --
    dua halaman kosong/scan "cocok" secara trivial (sama-sama string kosong) dan itu
    bukan duplikasi sungguhan.
    """
    n = len(pages)
    if n < 2 or n % 2 != 0:
        return 0
    half = n // 2
    if text_hashes[:half] != text_hashes[half:]:
        return 0
    if any(p.native_chars < config.PAGE_NATIVE_MIN_CHARS for p in pages[:half]):
        return 0
    return half


def profile_document(pdf_path: str, max_pages: int = None) -> DocumentProfile:
    import pymupdf as fitz

    min_chars = config.PAGE_NATIVE_MIN_CHARS
    pages: list[PageProfile] = []
    text_hashes: list[int] = []
    with fitz.open(pdf_path) as doc:
        limit = min(len(doc), max_pages) if max_pages else len(doc)
        for idx in range(limit):
            page = doc[idx]
            text = page.get_text("text").strip()
            chars = len(text)
            text_hashes.append(hash(text))
            area = max(page.rect.width * page.rect.height, 1.0)
            image_area = 0.0
            for info in page.get_image_info():
                x0, y0, x1, y1 = info.get("bbox", (0, 0, 0, 0))
                image_area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
            coverage = min(1.0, image_area / area)
            # Halaman dengan sedikit teks native, atau didominasi gambar dengan teks tipis (hasil
            # scan + stempel).
            needs_ocr = chars < min_chars or (coverage > 0.6 and chars < min_chars * 3)
            pages.append(PageProfile(idx + 1, chars, round(coverage, 3), needs_ocr))

    count = len(pages)
    ocr_pages = [p.page_number for p in pages if p.needs_ocr]
    scanned_ratio = len(ocr_pages) / max(count, 1)
    kind = "scanned" if scanned_ratio >= 0.8 else "native" if not ocr_pages else "mixed"
    return DocumentProfile(
        file_name=Path(pdf_path).name,
        page_count=count,
        kind=kind,
        native_text_ratio=round(1 - scanned_ratio, 3),
        scanned_page_ratio=round(scanned_ratio, 3),
        ocr_pages=ocr_pages,
        pages=pages,
        duplicate_block_pages=_detect_duplicate_block(pages, text_hashes),
    )
