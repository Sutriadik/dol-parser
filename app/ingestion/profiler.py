"""
Open ADE: Document profiler (Plan §7.1).

Menentukan karakteristik dokumen per halaman SEBELUM memilih engine, memakai PyMuPDF
saja (cepat, tanpa model). Hasilnya dipakai router untuk memutuskan apakah OCR perlu.

Sebelumnya Docling selalu dijalankan dengan do_ocr=True, termasuk untuk PDF digital:
model OCR tetap dimuat dan menjalankan OCR pada setiap gambar/logo halaman tanpa manfaat.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import config
from app.logger import logger


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
    # Teks yang ada di lapisan PDF tapi tidak tergambar di halaman: [{"page", "bbox", "text"}],
    # bbox dalam poin PDF. Lihat find_hidden_text.
    hidden_text: list[dict[str, Any]] = field(default_factory=list)

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


# Teks tersembunyi dicari dengan membandingkan render halaman sebelum dan sesudah seluruh teksnya
# dihapus. Span yang tidak mengubah satu piksel pun berarti tidak terlihat oleh PM, apa pun
# sebabnya: ditimpa kotak atau gambar, warnanya sama dengan latar, terpotong batas sel, atau
# terletak di luar halaman. Kasus nyata: SPH vendor yang baris PPN dan totalnya ditimpa kotak
# putih, sehingga Docling membaca angka yang tidak tampak di halaman.
_HIDDEN_RENDER_DPI = 72
_HIDDEN_PIXEL_DIFF = 40  # beda kanal warna (0-255) yang dihitung sebagai goresan huruf
_TEXT_INVISIBLE_MODE = 3  # render mode 3: lapisan pencarian hasil OCR di atas gambar scan


def _span_text(span: dict[str, Any]) -> str:
    return "".join(chr(c[0]) for c in span["chars"]).strip()


def _render_rgb(page):
    import numpy as np

    pix = page.get_pixmap(dpi=_HIDDEN_RENDER_DPI, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)


def _render_tanpa_teks(doc, page_index: int):
    """Render halaman `page_index` setelah seluruh teksnya dihapus (gambar & garis tetap)."""
    import pymupdf as fitz

    salinan = fitz.open()
    try:
        salinan.insert_pdf(doc, from_page=page_index, to_page=page_index)
        halaman = salinan[0]
        # Satu redaksi seluas halaman, bukan satu per span: biaya add_redact_annot naik
        # kuadratik terhadap jumlah anotasi (halaman 791 span sempat butuh 12 detik). Diperluas
        # supaya teks yang meluap ke luar halaman ikut terhapus.
        halaman.add_redact_annot(halaman.rect + (-1e4, -1e4, 1e4, 1e4), fill=False)
        halaman.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )
        return _render_rgb(halaman)
    finally:
        salinan.close()


def _bertumpuk(a: tuple, b: tuple) -> bool:
    """Irisan ≥50% luas kotak yang lebih kecil. Span bersebelahan sering bersinggungan sepersekian
    poin karena kerning; itu bukan teks yang diketik di atas teks lain."""
    lebar = min(a[2], b[2]) - max(a[0], b[0])
    tinggi = min(a[3], b[3]) - max(a[1], b[1])
    if lebar <= 0 or tinggi <= 0:
        return False
    kecil = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return lebar * tinggi >= 0.5 * max(kecil, 1e-6)


def find_hidden_text(doc, page_index: int) -> list[dict[str, Any]]:
    """Span teks halaman `page_index` (0-based) yang tidak tergambar, satu dict per span."""
    import numpy as np

    page = doc[page_index]
    spans = [
        s
        for s in page.get_texttrace()
        if s["type"] != _TEXT_INVISIBLE_MODE and any(ch.isalnum() for ch in _span_text(s))
    ]
    if not spans:
        return []

    sebelum = _render_rgb(page).astype(np.int16)
    skala = _HIDDEN_RENDER_DPI / 72
    tinggi, lebar = sebelum.shape[:2]

    def tergambar(s: dict[str, Any], sesudah) -> bool:
        x0, y0, x1, y1 = s["bbox"]
        c0, r0 = max(int(x0 * skala), 0), max(int(y0 * skala), 0)
        c1, r1 = min(int(x1 * skala) + 1, lebar), min(int(y1 * skala) + 1, tinggi)
        # Daerah kosong setelah dipotong batas halaman = teks di luar halaman: tidak tergambar.
        if c1 <= c0 or r1 <= r0:
            return False
        beda = np.abs(sebelum[r0:r1, c0:c1] - sesudah[r0:r1, c0:c1]).max(axis=2)
        return bool((beda > _HIDDEN_PIXEL_DIFF).any())

    # Indeks span per pita 10 poin supaya cek tumpukan tidak membandingkan semua pasangan
    # (halaman padat bisa berisi ribuan span).
    pita: dict[int, list[dict[str, Any]]] = {}
    for s in spans:
        for k in range(int(s["bbox"][1] // 10), int(s["bbox"][3] // 10) + 1):
            pita.setdefault(k, []).append(s)

    def ada_yang_menumpuk(s: dict[str, Any]) -> bool:
        kandidat = {
            id(t): t
            for k in range(int(s["bbox"][1] // 10), int(s["bbox"][3] // 10) + 1)
            for t in pita.get(k, [])
        }
        return any(t is not s and _bertumpuk(s["bbox"], t["bbox"]) for t in kandidat.values())

    tanpa_semua = _render_tanpa_teks(doc, page_index).astype(np.int16)
    penutup = None
    hasil = []
    for s in spans:
        if tergambar(s, tanpa_semua):
            # Teks yang ditimpa kotak lalu diketik ulang tepat di atasnya tetap "berubah" karena
            # teks barunya, dan redaksi tidak bisa membuang satu span saja dari kotak yang sama.
            # Untuk span yang bertumpuk, pakai urutan gambar: ia tertutup bila sesudahnya ada
            # kotak berisi yang tidak tembus pandang menimpanya. Gambar (stempel, logo) tidak
            # dihitung karena sering berlatar transparan di atas nama penandatangan.
            if not ada_yang_menumpuk(s):
                continue
            if penutup is None:
                # Hanya persegi berisi. `rect` sebuah path adalah kotak luarnya, jadi garis-garis
                # tabel yang digambar sebagai satu path tampak seperti kotak selebar baris.
                penutup = [
                    (d["seqno"], item[1])
                    for d in page.get_drawings()
                    if d.get("fill") is not None and (d.get("fill_opacity") or 0) >= 0.99
                    for item in d["items"]
                    if item[0] == "re"
                ]
            if not _ditimpa(s, penutup):
                continue
        hasil.append(
            {
                "page": page_index + 1,
                "bbox": [round(v, 1) for v in s["bbox"]],
                "text": _span_text(s),
            }
        )
    return hasil


def _ditimpa(span: dict[str, Any], penutup: list[tuple]) -> bool:
    """True bila ≥90% kotak span tertutup kotak berisi yang digambar sesudahnya."""
    import pymupdf as fitz

    kotak = fitz.Rect(span["bbox"])
    luas = max(kotak.get_area(), 1e-6)
    return any(
        seqno > span["seqno"] and (kotak & rect).get_area() / luas >= 0.9 for seqno, rect in penutup
    )


def strip_hidden_text(pdf_path: str, hidden: list[dict[str, Any]], out_path: str) -> bool:
    """
    Tulis salinan PDF tanpa teks tersembunyi ke `out_path`. False bila membuangnya ikut
    menghapus teks yang terlihat (redaksi PyMuPDF bekerja per kotak, bukan per span). Dalam
    kasus itu salinan tidak ditulis dan pemanggil membaca PDF aslinya.
    """
    import pymupdf as fitz

    per_halaman: dict[int, list[dict[str, Any]]] = {}
    for h in hidden:
        per_halaman.setdefault(h["page"] - 1, []).append(h)

    with fitz.open(pdf_path) as doc:
        for idx, temuan in per_halaman.items():
            page = doc[idx]
            tersembunyi = {tuple(h["bbox"]) for h in temuan}
            terlihat = [
                _span_text(s)
                for s in page.get_texttrace()
                if tuple(round(v, 1) for v in s["bbox"]) not in tersembunyi and _span_text(s)
            ]
            for h in temuan:
                page.add_redact_annot(fitz.Rect(h["bbox"]), fill=False)
            page.apply_redactions(
                images=fitz.PDF_REDACT_IMAGE_NONE,
                graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                text=fitz.PDF_REDACT_TEXT_REMOVE,
            )
            sisa = " ".join(_span_text(s) for s in page.get_texttrace())
            if any(t not in sisa for t in terlihat):
                return False
        doc.save(out_path, garbage=3, deflate=True)
    return True


def profile_document(pdf_path: str, max_pages: int = None) -> DocumentProfile:
    import pymupdf as fitz

    min_chars = config.PAGE_NATIVE_MIN_CHARS
    pages: list[PageProfile] = []
    text_hashes: list[int] = []
    hidden_text: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        limit = min(len(doc), max_pages) if max_pages else len(doc)
        for idx in range(limit):
            page = doc[idx]
            text = page.get_text("text").strip()
            chars = len(text)
            if chars:
                # Pemeriksaan tambahan: galatnya tidak boleh menggagalkan profil, karena tanpa
                # profil engine menganggap berkas bukan PDF dan tidak menjalankan OCR.
                try:
                    hidden_text.extend(find_hidden_text(doc, idx))
                except Exception as e:
                    logger.warning(f"Cek teks tersembunyi hlm {idx + 1} dilewati: {e}")
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
        hidden_text=hidden_text,
    )
