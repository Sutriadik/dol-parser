"""
Berkas berisi dua salinan dokumen yang sama (kasus nyata:
kontrak 8 halaman dipindai dua kali ke satu PDF 16 halaman).
"""

from app.schemas.evidence import ValidationReport
from app.services.duplicate_pages import PAGE_BREAK, drop_duplicate_copy, find_duplicate_tail
from app.validation.rules import note_duplicate_pages

HALAMAN = [
    "kontrak kerjasama pengadaan perpanjangan firewall pihak pertama universitas contoh",
    "pasal harga nilai kontrak sebesar enam ratus empat puluh dua juta rupiah",
    "pasal cara pembayaran termin rekening bank mandiri nomor rekening",
    "lampiran rincian harga firewall premium bundle software upgrade",
]


def _md(pages):
    return f"\n\n{PAGE_BREAK}\n\n".join(pages)


def test_salinan_utuh_di_belakang_terdeteksi():
    # Salinan kedua dengan urutan kata berbeda -- seperti OCR yang membaca ulang halaman.
    salinan = [" ".join(reversed(p.split())) for p in HALAMAN]
    assert find_duplicate_tail(HALAMAN + salinan) == 4


def test_hanya_salinan_pertama_yang_diekstrak():
    md, dibuang = drop_duplicate_copy(_md(HALAMAN + HALAMAN))
    assert dibuang == [5, 6, 7, 8]
    assert md.count(PAGE_BREAK) == 3
    assert "lampiran rincian harga" in md


def test_dokumen_biasa_tidak_disentuh():
    md = _md(HALAMAN)
    assert drop_duplicate_copy(md) == (md, [])


def test_satu_halaman_kembar_tidak_dianggap_salinan():
    """Halaman kembar tunggal (mis. lampiran dicetak dua kali) bukan salinan dokumen."""
    assert find_duplicate_tail(HALAMAN + HALAMAN[-1:]) is None


def test_pm_diberi_tahu_halaman_yang_tidak_diekstrak():
    kosong = ValidationReport(status="pass", rule_version="uji", checked_rules=[], issues=[])
    report = note_duplicate_pages(kosong, [9, 10, 11, 12, 13, 14, 15, 16])
    assert report.status == "warn"
    assert "halaman 9-16" in report.issues[0].message
    assert note_duplicate_pages(kosong, []).status == "pass"
