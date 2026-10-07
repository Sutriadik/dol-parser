"""
Test untuk app.parsers.markdown_refiner — deteksi kerusakan OCR berat.

Hanya menguji fungsi deterministik (_detect_severe_ocr_corruption).
Fungsi yang memanggil Ollama (refine_markdown) diuji terpisah
karena butuh LLM aktif.
"""

from app.parsers.markdown_refiner import _detect_severe_ocr_corruption


class TestDetectSevereOcrCorruption:
    """Deteksi pola teks rusak akibat scan tinta pudar / OCR buruk."""

    def test_clean_text_not_corrupt(self):
        assert _detect_severe_ocr_corruption("Berdasarkan hasil negosiasi harga") is False

    def test_empty_string(self):
        assert _detect_severe_ocr_corruption("") is False

    def test_none_not_corrupt(self):
        assert _detect_severe_ocr_corruption(None) is False

    def test_known_corruption_ber_aal_ana(self):
        assert _detect_severe_ocr_corruption("Ber aal ana ea a a al a aan") is True

    def test_known_corruption_ea_a_a(self):
        assert _detect_severe_ocr_corruption("Dokumen dengan ea a a al a aan di dalamnya") is True

    def test_known_corruption_menn_aan(self):
        assert _detect_severe_ocr_corruption("menn aan mannan kelompok") is True

    def test_single_letter_runaway_pattern(self):
        assert _detect_severe_ocr_corruption("pean ean be do ke lo ba") is True

    def test_normal_abbreviations_not_flagged(self):
        """Singkatan jabatan normal (S.H., M.M.) bukan kerusakan OCR."""
        assert _detect_severe_ocr_corruption("Ir. H.M. Soekarno, S.H., M.M.") is False

    def test_normal_contract_text(self):
        text = (
            "Pasal 7 ayat (2): Jangka waktu pelaksanaan pekerjaan selama "
            "30 (tiga puluh) hari kalender terhitung sejak tanggal diterbitkannya "
            "Surat Perintah Mulai Kerja (SPMK)."
        )
        assert _detect_severe_ocr_corruption(text) is False

    def test_table_markdown_not_corrupt(self):
        text = "| No | Uraian | Qty | Harga |\n|---|---|---|---|\n| 1 | Server | 2 | 50.000.000 |"
        assert _detect_severe_ocr_corruption(text) is False
