"""
Bacaan OCR kedua untuk nomor dokumen, NPWP, dan rekening di halaman scan.

Kasus nyata: nomor kontrak sebuah Nota Pesanan (scan) terbaca RapidOCR tanpa satu titik.
LLM menyalinnya apa adanya, lalu grounding menemukan nilai itu persis di teks OCR yang sama
dan memberi status bukti_kuat. Bukti yang berasal dari bacaan yang sama tidak bisa menangkap
salah baca. Tesseract membaca potongan gambarnya sekali lagi; bila hasilnya berbeda, status
turun ke perlu_dicek dan PM melihat alasannya.

Nilai di sini rekaan.
"""

import shutil

import pymupdf
import pytest

from app.evidence import locator
from app.evidence.locator import verify_identifiers_second_ocr
from app.schemas.common import BoundingBox
from app.schemas.evidence import FieldEvidence, FieldStatus, ValidationReport
from app.validation.rules import note_second_ocr


def _ev(field, value, status=FieldStatus.AUTO_VERIFIED, page=1):
    return FieldEvidence(
        field=field,
        value=value,
        source_document="uji.pdf",
        page=page,
        bbox=BoundingBox(xmin=0.1, ymin=0.1, xmax=0.9, ymax=0.2),
        evidence_text=str(value),
        status=status,
        confidence=0.95,
    )


@pytest.fixture
def pdf_kosong(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "uji.pdf"
    doc.save(path)
    return str(path)


@pytest.fixture
def bacaan(monkeypatch):
    """Ganti Tesseract dengan teks tetap supaya tes tidak bergantung pada biner OCR."""

    def pasang(teks):
        monkeypatch.setattr(locator, "_tesseract_tersedia", lambda: True)
        monkeypatch.setattr(locator, "_baca_potongan", lambda *a, **k: teks)

    return pasang


def test_nomor_yang_berbeda_dari_bacaan_kedua_turun_ke_perlu_dicek(pdf_kosong, bacaan):
    bacaan("Nomor : K.TEL.010203/HK.810/T1R/2031")
    evidence = [_ev("Nomor Kontrak Kerja", "K.TEL010203/HK.810/T1R/2031")]

    hasil, berbeda, ringkas = verify_identifiers_second_ocr(evidence, pdf_kosong, [1])

    assert hasil[0].status == FieldStatus.REVIEW_REQUIRED
    assert "ocr_kedua_berbeda" in hasil[0].issues
    assert hasil[0].confidence <= locator.SECOND_OCR_MAX_CONFIDENCE
    assert berbeda[0]["field"] == "Nomor Kontrak Kerja"
    assert "K.TEL.010203" in berbeda[0]["bacaan"]
    assert ringkas == {"mesin": "tesseract", "diperiksa": 1, "berbeda": 1}


def test_nomor_yang_sama_dengan_bacaan_kedua_tetap_bukti_kuat(pdf_kosong, bacaan):
    bacaan("Nomor : K.TEL.010203/HK.810/T1R/2031\nTanggal 2 Januari 2031")
    evidence = [_ev("Nomor Kontrak Kerja", "K.TEL.010203/HK.810/T1R/2031")]

    hasil, berbeda, _ = verify_identifiers_second_ocr(evidence, pdf_kosong, [1])

    assert hasil[0].status == FieldStatus.AUTO_VERIFIED
    assert berbeda == []


def test_halaman_digital_dan_field_bukan_identifier_tidak_dibaca_ulang(pdf_kosong, bacaan):
    bacaan("isi lain sama sekali")
    evidence = [
        _ev("Nomor Kontrak Kerja", "12/AB/2031", page=2),  # halaman 2 bukan halaman scan
        _ev("Nama Pekerjaan", "Pengadaan Lisensi"),
        _ev("List Item/Barang[0].Nomor Item", "1"),
        _ev("NPWP", "01.234.567.8-901.000", status=FieldStatus.REVIEW_REQUIRED),
    ]

    hasil, berbeda, ringkas = verify_identifiers_second_ocr(evidence, pdf_kosong, [1])

    assert [e.status for e in hasil] == [e.status for e in evidence]
    assert berbeda == [] and ringkas["diperiksa"] == 0


def test_tanpa_tesseract_tidak_mengubah_apa_pun(pdf_kosong, monkeypatch):
    monkeypatch.setattr(locator, "_tesseract_tersedia", lambda: False)
    evidence = [_ev("Nomor Kontrak Kerja", "K.TEL010203/HK.810")]

    hasil, berbeda, ringkas = verify_identifiers_second_ocr(evidence, pdf_kosong, [1])

    assert hasil[0].status == FieldStatus.AUTO_VERIFIED
    assert berbeda == []
    assert ringkas["mesin"] is None


def test_alasan_bacaan_kedua_masuk_validasi_per_field():
    laporan = ValidationReport(status="pass", rule_version="uji")
    berbeda = [
        {
            "field": "Nomor Kontrak Kerja",
            "value": "K.TEL010203/HK.810",
            "page": 1,
            "bacaan": "Nomor : K.TEL.010203/HK.810",
        }
    ]

    hasil = note_second_ocr(laporan, berbeda)

    isu = hasil.issues_for("Nomor Kontrak Kerja")
    assert len(isu) == 1
    assert "K.TEL.010203/HK.810" in isu[0].message
    assert hasil.status == "warn"


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="biner tesseract tidak terpasang")
def test_tesseract_sungguhan_membedakan_titik_yang_hilang(tmp_path):
    """Halaman 'scan' rekaan: teks dirender ke gambar lalu ditempel sebagai halaman PDF."""
    sumber = pymupdf.open()
    halaman = sumber.new_page(width=595, height=200)
    halaman.insert_text((40, 100), "Nomor: K.TEL.010203/HK.810/2031", fontsize=22)
    gambar = halaman.get_pixmap(dpi=200)
    scan = pymupdf.open()
    scan.new_page(width=595, height=200).insert_image(halaman.rect, pixmap=gambar)
    path = tmp_path / "scan.pdf"
    scan.save(path)

    def ev(value):
        e = _ev("Nomor Kontrak Kerja", value)
        e.bbox = BoundingBox(xmin=0.02, ymin=0.3, xmax=0.98, ymax=0.6)
        return e

    hasil, berbeda, _ = verify_identifiers_second_ocr(
        [ev("K.TEL.010203/HK.810/2031"), ev("K.TEL010203/HK.810/2031")], str(path), [1]
    )
    assert [e.status for e in hasil] == [FieldStatus.AUTO_VERIFIED, FieldStatus.REVIEW_REQUIRED]
    assert len(berbeda) == 1


def test_tesseract_macet_tidak_menggagalkan_dokumen(pdf_kosong, monkeypatch):
    import subprocess

    def macet(*_a, **_k):
        raise subprocess.TimeoutExpired("tesseract", 60)

    monkeypatch.setattr(locator, "_tesseract_tersedia", lambda: True)
    monkeypatch.setattr(locator, "_baca_potongan", macet)
    evidence = [_ev("Nomor Kontrak Kerja", "K.TEL010203/HK.810")]

    hasil, berbeda, ringkas = verify_identifiers_second_ocr(evidence, pdf_kosong, [1])

    assert hasil[0].status == FieldStatus.AUTO_VERIFIED
    assert berbeda == []
    assert ringkas["galat"] == 1 and ringkas["diperiksa"] == 0
