"""
Teks yang ada di lapisan PDF tetapi tidak tergambar di halaman.

Kasus nyata: pada sebuah SPH vendor, baris "PPN 11%" dan "Total Beban Pekerjaan" ditimpa
kotak putih. PM hanya melihat "Total Harga", sementara Docling membaca lapisan teks dan
LLM mengisi Grand Total dengan angka yang tidak terlihat siapa pun. Pada dokumen lain ada
teks putih "jkk" yang menempel di belakang nomor dokumen.

Semua PDF di sini dibangkitkan di dalam tes; tidak ada data klien.
"""

import pymupdf
import pytest

from app.ingestion.profiler import find_hidden_text, profile_document, strip_hidden_text
from app.schemas.evidence import ValidationReport
from app.validation.rules import note_hidden_text


def _buat_pdf(tmp_path, gambar) -> str:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    gambar(page)
    path = tmp_path / "uji.pdf"
    doc.save(path)
    doc.close()
    return str(path)


def _teks_semua(path: str) -> str:
    with pymupdf.open(path) as doc:
        return " ".join(page.get_text() for page in doc)


def _sph_dengan_baris_ditutup(page):
    page.insert_text((72, 100), "Total Harga 100.000.000", fontsize=11)
    page.insert_text((72, 130), "PPN 11% 11.000.000", fontsize=11)
    page.insert_text((72, 150), "Total Beban Pekerjaan 111.000.000", fontsize=11)
    # Kotak putih digambar SETELAH teks, jadi menimpanya.
    page.draw_rect(pymupdf.Rect(60, 118, 420, 156), color=None, fill=(1, 1, 1))


def test_teks_yang_ditutup_kotak_putih_terdeteksi(tmp_path):
    path = _buat_pdf(tmp_path, _sph_dengan_baris_ditutup)
    with pymupdf.open(path) as doc:
        temuan = find_hidden_text(doc, 0)
    gabungan = " ".join(t["text"] for t in temuan)
    assert "PPN" in gabungan
    assert "Total Beban" in gabungan
    assert "Total Harga" not in gabungan


def test_teks_putih_di_latar_putih_terdeteksi(tmp_path):
    def gambar(page):
        page.insert_text((72, 100), "Nomor: 12/AB/2026", fontsize=11)
        page.insert_text((300, 100), "jkk", fontsize=11, color=(1, 1, 1))

    with pymupdf.open(_buat_pdf(tmp_path, gambar)) as doc:
        temuan = find_hidden_text(doc, 0)
    assert [t["text"] for t in temuan] == ["jkk"]


def test_lapisan_ocr_tak_kasat_mata_tidak_dianggap_tersembunyi(tmp_path):
    """PDF hasil scan yang sudah di-OCR menaruh teks render mode 3 di atas gambar. Itu
    lapisan pencarian yang sah, bukan teks yang disembunyikan."""

    def gambar(page):
        page.insert_text((72, 100), "lapisan pencarian hasil OCR", fontsize=11, render_mode=3)

    with pymupdf.open(_buat_pdf(tmp_path, gambar)) as doc:
        assert find_hidden_text(doc, 0) == []


def test_teks_yang_terlihat_tidak_ditandai(tmp_path):
    def gambar(page):
        page.insert_text((72, 100), "Pihak Pertama", fontsize=11)
        page.insert_text((72, 130), "teks abu tipis tetap terbaca", fontsize=11, color=(0.4,) * 3)

    with pymupdf.open(_buat_pdf(tmp_path, gambar)) as doc:
        assert find_hidden_text(doc, 0) == []


def test_garis_tabel_dan_span_bersinggungan_bukan_penutup(tmp_path):
    """Kasus nyata (surat pesanan): span "Air" dan "pulutan control" bersinggungan karena
    kerning, lalu garis sel tabel digambar sebagai satu path selebar baris. Teksnya terlihat."""

    def gambar(page):
        page.insert_text((72, 100), "Air", fontsize=11)
        page.insert_text((89.3, 100), "pulutan control", fontsize=11)
        garis = page.new_shape()
        for x in (60, 200, 400):
            garis.draw_rect(pymupdf.Rect(x, 88, x + 0.5, 104))
        garis.draw_rect(pymupdf.Rect(60, 103.5, 400, 104))
        garis.finish(color=None, fill=(0, 0, 0))
        garis.commit()

    with pymupdf.open(_buat_pdf(tmp_path, gambar)) as doc:
        assert find_hidden_text(doc, 0) == []


def test_teks_di_luar_batas_halaman_terdeteksi(tmp_path):
    def gambar(page):
        page.insert_text((72, 100), "isi halaman", fontsize=11)
        page.insert_text((72, 900), "catatan yang meluap ke bawah halaman", fontsize=11)

    with pymupdf.open(_buat_pdf(tmp_path, gambar)) as doc:
        assert [t["text"] for t in find_hidden_text(doc, 0)] == [
            "catatan yang meluap ke bawah halaman"
        ]


def test_profil_mencatat_teks_tersembunyi_per_halaman(tmp_path):
    profil = profile_document(_buat_pdf(tmp_path, _sph_dengan_baris_ditutup))
    assert {t["page"] for t in profil.hidden_text} == {1}


def test_salinan_bersih_membuang_teks_tersembunyi_saja(tmp_path):
    path = _buat_pdf(tmp_path, _sph_dengan_baris_ditutup)
    profil = profile_document(path)
    bersih = tmp_path / "bersih.pdf"

    assert strip_hidden_text(path, profil.hidden_text, str(bersih)) is True
    teks = _teks_semua(str(bersih))
    assert "Total Harga 100.000.000" in teks
    assert "PPN" not in teks
    assert "111.000.000" not in teks


def test_salinan_bersih_batal_bila_teks_terlihat_ikut_terhapus(tmp_path):
    """Teks putih yang menumpuk di atas teks terlihat tidak bisa dibuang tanpa ikut
    menghapus teks terlihatnya. Dalam kasus itu salinan bersih tidak dipakai."""

    def gambar(page):
        page.insert_text((72, 100), "Nomor 12/AB/2026", fontsize=11)
        page.draw_rect(pymupdf.Rect(70, 88, 200, 104), color=None, fill=(1, 1, 1))
        page.insert_text((72, 100), "Nomor 99/ZZ/2026", fontsize=11)

    path = _buat_pdf(tmp_path, gambar)
    profil = profile_document(path)
    assert profil.hidden_text, "teks yang ditimpa kotak harus tetap terdeteksi"
    assert strip_hidden_text(path, profil.hidden_text, str(tmp_path / "bersih.pdf")) is False


def test_engine_membaca_salinan_tanpa_teks_tersembunyi(tmp_path, monkeypatch):
    from pathlib import Path

    from app.config import config
    from app.schemas.common import DocumentStructure, LandingAIParsedResponse, ParseMetadata
    from app.services.engine import OpenADEEngine

    path = _buat_pdf(tmp_path, _sph_dengan_baris_ditutup)
    dibaca = {}

    class DoclingPalsu:
        def parse(self, pdf_path, **_):
            dibaca["nama"] = Path(pdf_path).name
            dibaca["teks"] = _teks_semua(pdf_path)
            return LandingAIParsedResponse(
                markdown=dibaca["teks"],
                metadata=ParseMetadata(job_id="x", page_count=1, output_markdown_chars=1),
                structure=DocumentStructure(children=[]),
            )

    engine = OpenADEEngine()
    monkeypatch.setattr(engine, "_docling", DoclingPalsu())
    monkeypatch.setattr(config, "ENABLE_LLM_MARKDOWN_REFINER", False)

    parsed = engine.parse(path, ocr="rapidocr")

    assert dibaca["nama"] == "uji.pdf"
    assert "Total Harga" in dibaca["teks"] and "PPN" not in dibaca["teks"]
    assert parsed.metadata.teks_tersembunyi_dibuang is True
    assert {t["page"] for t in parsed.metadata.teks_tersembunyi} == {1}


@pytest.mark.parametrize("dibuang", [True, False])
def test_peringatan_teks_tersembunyi_masuk_catatan_validasi(dibuang):
    laporan = ValidationReport(status="pass", rule_version="uji")
    temuan = [{"page": 1, "bbox": [94, 452, 515, 463], "text": "PPN 11% 11.000.000"}]

    hasil = note_hidden_text(laporan, temuan, dibuang=dibuang)

    assert hasil.status == "warn"
    pesan = hasil.issues[-1].message
    assert "halaman 1" in pesan
    assert "PPN 11%" in pesan
    assert ("tidak ikut dibaca" in pesan) is dibuang


def test_tanpa_teks_tersembunyi_tidak_ada_peringatan():
    laporan = ValidationReport(status="pass", rule_version="uji")
    hasil = note_hidden_text(laporan, [], dibuang=False)
    assert hasil.status == "pass"
    assert hasil.issues == []
    assert "teks_tersembunyi" in hasil.checked_rules


def test_galat_cek_teks_tersembunyi_tidak_menggagalkan_profil(tmp_path, monkeypatch):
    """Tanpa profil, engine menganggap berkas bukan PDF dan OCR tidak dijalankan."""
    from app.ingestion import profiler

    def rusak(*_a, **_k):
        raise RuntimeError("PDF aneh")

    monkeypatch.setattr(profiler, "find_hidden_text", rusak)
    profil = profile_document(_buat_pdf(tmp_path, _sph_dengan_baris_ditutup))
    assert profil.page_count == 1
    assert profil.hidden_text == []
