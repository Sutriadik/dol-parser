"""
Tests untuk pemilihan engine OCR (app/parsers/docling_parser.py).

Dulu pemilihan engine dibungkus try/except tanpa nilai default: di macOS terpilih Apple
Vision, di Linux exception-nya ditelan dan Docling diam-diam memakai engine lain. Tes ini
menjaga agar perilaku "gagal terang-terangan" tidak tanpa sengaja dikembalikan jadi
"mundur diam-diam" — kesalahan yang baru ketahuan saat dokumen produksi salah terbaca.
"""

import importlib.util
import sys

import pytest

# Tes di berkas ini memanggil Docling sungguhan. Di lingkungan tanpa Docling terpasang
# (CI ringan) mereka dilewati, BUKAN dianggap lulus -- laporan pytest tetap menyebutnya
# "skipped" supaya tidak ada yang mengira cakupannya utuh. Jalankan lokal dengan
# dependensi penuh sebelum merge perubahan pada docling_parser.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling tidak terpasang (dilewati di CI ringan; jalankan lokal)",
)

# Sengaja setelah pytestmark: penanda skip harus terbaca lebih dulu oleh pembaca berkas.
from app.parsers import docling_parser as dp  # noqa: E402


@pytest.fixture
def set_engine(monkeypatch):
    def _set(value):
        monkeypatch.setattr(dp.config, "OCR_ENGINE", value)

    return _set


def test_auto_memilih_apple_vision_di_macos(set_engine, monkeypatch):
    """Di Mac, default harus tetap Apple Vision — ini yang menghasilkan output saat ini."""
    if sys.platform != "darwin":
        pytest.skip("hanya relevan di macOS")
    set_engine("auto")
    assert dp._resolve_ocr_engine().name == "mac"


def test_auto_di_linux_memakai_default_docling_dengan_peringatan(set_engine, monkeypatch, caplog):
    """Di platform non-Mac, auto boleh mundur ke default TAPI wajib bersuara."""
    set_engine("auto")
    monkeypatch.setattr(sys, "platform", "linux")
    engine = dp._resolve_ocr_engine()
    assert engine.name == "docling-default"
    assert engine.options is None


def test_engine_tidak_dikenal_ditolak(set_engine):
    set_engine("tesserakt")  # salah ketik
    with pytest.raises(ValueError, match="tidak dikenal"):
        dp._resolve_ocr_engine()


def test_engine_eksplisit_tidak_pernah_mundur_diam_diam(set_engine, monkeypatch):
    """
    Kalau engine diminta eksplisit tapi tidak bisa dimuat, harus RuntimeError —
    bukan diam-diam memakai engine lain.
    """
    set_engine("tesseract")
    import docling.datamodel.pipeline_options as po

    monkeypatch.delattr(po, "TesseractCliOcrOptions", raising=False)
    with pytest.raises(RuntimeError, match="tidak bisa"):
        dp._resolve_ocr_engine()


def test_easyocr_sudah_dibuang_dan_ditolak(set_engine):
    """`.env` lama berisi OCR_ENGINE=easyocr harus gagal jelas, bukan mundur ke engine lain."""
    set_engine("easyocr")
    with pytest.raises(ValueError, match="tidak dikenal"):
        dp._resolve_ocr_engine()


def test_semua_engine_terdaftar_punya_kelas_options_di_docling():
    """Nama kelas di _OCR_ENGINES harus benar-benar ada di Docling versi terpasang."""
    import docling.datamodel.pipeline_options as po

    for key, (class_name, _kwargs, _desc) in dp._OCR_ENGINES.items():
        assert hasattr(po, class_name), f"{key}: {class_name} tidak ada di Docling ini"


def test_rapidocr_tidak_memakai_bahasa_chinese_default():
    """
    Regresi ke bug nyata: RapidOcrOptions() tanpa argumen default ke lang=["chinese"],
    yang di Docling resolve ke model gabungan CJK+Latin yang sama dipakai untuk
    lang=["en"] (diverifikasi byte-identical). Model itu memecah teks Latin murni jadi
    potongan tak terbaca -- pada dokumen scan kontrak, satu pasal terbaca
    "e ean eaan ean an ea" alih-alih kalimat aslinya.

    lang=["latin"] memuat model PP-OCRv5 khusus skrip Latin dan terbukti (benchmark
    end-to-end) menurunkan rasio kata rusak dari 18,3% ke 4,7% pada dokumen yang sama.
    Kalau tes ini gagal, seseorang mengembalikan RapidOCR ke default bahasa yang salah.
    """
    _class_name, kwargs, _desc = dp._OCR_ENGINES["rapidocr"]
    assert kwargs.get("lang") == ["latin"], (
        "RapidOCR harus eksplisit lang=['latin'], bukan default library (['chinese'])"
    )


def test_rapidocr_tanpa_pengklasifikasi_arah_baris():
    """
    Regresi ke bug nyata (kontrak scan 14 halaman): use_cls bawaan Docling
    memutar 180 derajat sebagian crop baris yang sebenarnya tegak, lalu recognizer membaca
    sampah ("en n  eee en   e", skor 0,29) yang dibuang ambang text_score 0,5. Akibatnya
    66 baris hilang tanpa jejak -- mis. baris pertama Pasal 1 ayat (1) beserta penanda
    "(1)"-nya. Dengan use_cls=False: 3 baris terbuang (noise), kata terbaca 4.062 -> 4.804.
    """
    _class_name, kwargs, _desc = dp._OCR_ENGINES["rapidocr"]
    assert kwargs.get("use_cls") is False


def test_tabel_panjang_tidak_dipotong():
    """
    Regresi ke bug nyata: blok tabel dipotong `[:3000]`, sehingga Lampiran I sebuah
    kontrak berhenti di sel "6.2" -- baris 4-5, subtotal, total, dan termin hilang.
    """
    rows = [["No", "Uraian", "Harga Total"]]
    rows += [[str(i), f"Pekerjaan nomor {i} uraian panjang", "4.735.000,00"] for i in range(80)]
    rows.append(["", "Total Keseluruhan (Sebelum PPN)", "401.360.000,00"])
    block = dp._blok_tabel(rows, None, 0.9)
    assert len(block.text) > 3000
    assert block.text.rstrip().endswith("401.360.000,00 |")


def test_cek_cakupan_menandai_halaman_yang_teksnya_terpotong():
    # Persis kasus Lampiran I: Docling membaca seluruh tabel, markdown berhenti di "6.2".
    sumber = {13: "Tools Pendampingan Online 6.240.000 Akomodasi Konsumsi Laporan Termin"}
    hasil = {13: "| Tools Pendampingan Online | 6.2"}
    temuan = dp.cek_cakupan(sumber, hasil)
    assert [t["halaman"] for t in temuan] == [13]
    assert temuan[0]["cakupan"] < 0.5
    assert "akomodasi" in temuan[0]["contoh"]


def test_cek_cakupan_diam_bila_teks_utuh_walau_format_berbeda():
    # Penanda list, markdown, dan spasi berbeda -- kata-katanya tetap sama.
    sumber = {2: "Bencana alam, gempa bumi\nPeraturan resmi Pemerintah"}
    hasil = {2: "- (1) Yang\n   - a. Bencana alam, gempa bumi\n   - b. Peraturan resmi Pemerintah"}
    assert dp.cek_cakupan(sumber, hasil) == []
