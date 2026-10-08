"""
Nominal yang ditulis sebagai angka DAN terbilang yang sama lebih dipercaya dari tebakan LLM
-- tetapi hanya dipakai untuk menambal nilai yang tidak tertulis di dokumen.

Kasus nyata (kontrak pindaian dua salinan): dokumen menulis nilai kontrak beserta terbilangnya,
LLM mengembalikan 1.130.000.000 yang tidak ada di dokumen.
"""

from types import SimpleNamespace

from app.extractors.deterministic.numbers import confirmed_amounts
from app.extractors.ollama_client import OllamaExtractor

PASAL_6 = (
    "Nilai harga sebesar Rp... 642.375.000,- (Enam Ratus Empat Puluh Dua Juta Tiga Ratus "
    "Tujuh Puluh Lima Ribu Rupiah) harga sudah termasuk PPN."
)


def _fix(nilai, teks):
    obj = SimpleNamespace(total_harga_pekerjaan=nilai)
    OllamaExtractor._prefer_confirmed_amount(obj, "total_harga_pekerjaan", teks)
    return obj.total_harga_pekerjaan


def test_nominal_terkonfirmasi_terbilang_ditemukan():
    assert confirmed_amounts(PASAL_6) == [642375000.0]


def test_terbilang_yang_tidak_cocok_tidak_dipercaya():
    teks = "sebesar Rp 642.375.000,- (Enam Ratus Juta Rupiah)"
    assert confirmed_amounts(teks) == []


def test_nilai_karangan_llm_diganti():
    assert _fix(1_130_000_000.0, PASAL_6) == 642_375_000.0


def test_nilai_kosong_diisi():
    assert _fix(None, PASAL_6) == 642_375_000.0


def test_nilai_llm_yang_tertulis_di_dokumen_tidak_diganti():
    """Nilai LLM yang ada di dokumen dibiarkan, walau berbeda dari nominal terkonfirmasi."""
    teks = PASAL_6 + " Sub total Rp 665.000.000."
    assert _fix(665_000_000.0, teks) == 665_000_000.0


def test_lebih_dari_satu_nominal_terkonfirmasi_tidak_menebak():
    teks = PASAL_6 + " Termin I Rp 110.000.000,- (Seratus Sepuluh Juta Rupiah)."
    assert _fix(1_130_000_000.0, teks) == 1_130_000_000.0
