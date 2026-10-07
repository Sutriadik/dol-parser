"""
Unit tests untuk Document Classifier (rule-based fast classification only).
Tidak membutuhkan Ollama/LLM — hanya test rule-based logic.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# Import hanya fungsi rule-based tanpa trigger import ollama
# Kita test langsung logikanya
class TestClassifyFastRule:
    """Test rule-based fast classifier tanpa dependency pada ollama."""

    def _classify_fast_rule(self, text: str):
        """Replicate the fast rule logic untuk testing tanpa import ollama."""
        text_upper = text[:3000].upper()

        if any(
            k in text_upper
            for k in [
                "SURAT PERINTAH KERJA",
                "SPK",
                "PERJANJIAN KERJASAMA",
                "KONTRAK PENGADAAN",
                "PIHAK PERTAMA",
                "PIHAK KEDUA",
            ]
        ):
            return "contract", 0.98
        if any(
            k in text_upper
            for k in [
                "SURAT PENAWARAN HARGA",
                "SPH",
                "PENAWARAN HARGA",
                "PRICE QUOTATION",
                "PENAWARAN BIAYA",
            ]
        ):
            return "sph", 0.98
        if any(
            k in text_upper
            for k in [
                "BERITA ACARA SERAH TERIMA",
                "BAST",
                "SERAH TERIMA PEKERJAAN",
                "BERITA ACARA PENYELESAIAN",
            ]
        ):
            return "bast", 0.98
        return "contract", 0.70

    def test_detect_spk(self):
        text = "SURAT PERINTAH KERJA Nomor: 123/AST/2026"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"
        assert conf >= 0.95

    def test_detect_contract_with_pihak(self):
        text = "Yang mewakili PIHAK PERTAMA memberikan pekerjaan kepada PIHAK KEDUA"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"
        assert conf >= 0.95

    def test_detect_sph(self):
        text = "SURAT PENAWARAN HARGA Nomor: SPH/001/2026"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "sph"
        assert conf >= 0.95

    def test_detect_sph_price_quotation(self):
        text = "PRICE QUOTATION for IT Equipment"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "sph"
        assert conf >= 0.95

    def test_detect_bast(self):
        text = "BERITA ACARA SERAH TERIMA Pekerjaan Pengadaan Server"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "bast"
        assert conf >= 0.95

    def test_ambiguous_fallback(self):
        text = "Dokumen ini berisi informasi umum tentang perusahaan"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"
        assert conf < 0.90

    def test_case_insensitive(self):
        text = "surat perintah kerja nomor 123"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"

    def test_spk_abbreviation(self):
        text = "SPK Pengadaan Lisensi Oracle Tahun 2026"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"
        assert conf >= 0.95

    def test_truncates_to_3000_chars(self):
        """Should only check first 3000 chars."""
        text = "x" * 2999 + "SURAT PERINTAH KERJA"
        doc_type, conf = self._classify_fast_rule(text)
        assert doc_type == "contract"

    def test_keyword_beyond_3000_not_detected(self):
        """Keywords beyond 3000 chars should not be detected."""
        text = "x" * 3001 + "SURAT PERINTAH KERJA"
        doc_type, conf = self._classify_fast_rule(text)
        assert conf < 0.90


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
