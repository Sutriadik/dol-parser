"""
Perulangan tak berujung dari LLM tidak boleh menggagalkan seluruh dokumen.

Kasus nyata kontrak pindaian 16 halaman (30 Sep 2026): model menulis alamat Pihak Pertama
lalu "0812345678901234567890..." sampai kuota 4.096 token habis. JSON terpotong, pass 1
melempar ValidationError, dan dokumen 16 halaman gagal total setelah ±10 menit.
"""

from app.extractors import ollama_client as oc
from app.extractors.ollama_client import OllamaExtractor
from app.schemas.contract import ContractExtractionSchema

TERPOTONG = (
    '{\n  "Pihak Pertama": {\n    "Nama Perusahaan": "UNIVERSITAS CONTOH",\n'
    '    "Alamat": "Jalan Merpati Raya Nomor 1, 0812345678901234567890123456789'
)


def _schema():
    return OllamaExtractor._slim_schema(ContractExtractionSchema, oc.DETERMINISTIC_FIELDS)


# --------------------------------------------------------------------- lapis 1: batas panjang
def test_semua_field_teks_diberi_batas_panjang():
    capped = OllamaExtractor._cap_string_lengths(_schema())
    tanpa_batas = []

    def walk(node, path):
        if isinstance(node, dict):
            if node.get("type") == "string" and "maxLength" not in node:
                tanpa_batas.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(capped, "")
    assert tanpa_batas == []


def test_field_panjang_mendapat_batas_lebih_longgar():
    capped = OllamaExtractor._cap_string_lengths(_schema())
    alamat = capped["$defs"]["PihakDetail"]["properties"]["Alamat"]
    garansi = capped["properties"]["Garansi"]

    def batas(s):
        return next(v["maxLength"] for v in s.get("anyOf", [s]) if v.get("type") == "string")

    assert batas(alamat) == oc.TEXT_MAX_CHARS
    assert batas(garansi) == oc.LONG_TEXT_MAX_CHARS


def test_sisa_deret_angka_dibuang_dari_nilai():
    alamat = (
        "Jalan Merpati Raya Nomor 1 Kelurahan Contoh Semarang, Indonesia, "
        "081234567890123456789012345678901234567890"
    )
    bersih = OllamaExtractor._strip_runaway_digits({"Pihak Pertama": {"Alamat": alamat}})
    assert (
        bersih["Pihak Pertama"]["Alamat"]
        == "Jalan Merpati Raya Nomor 1 Kelurahan Contoh Semarang, Indonesia"
    )


def test_nomor_asli_tidak_ikut_dibuang():
    """NIK 16 digit, rekening, NPWP bertitik, dan nomor kontrak harus utuh."""
    nilai = {
        "NIK": "3273012345678901",
        "Rekening": "1230045678901",
        "NPWP": "12.345.678.9-012.345",
        "Nomor": "1234/ABC11/ABC-DEF/2026",
    }
    assert OllamaExtractor._strip_runaway_digits(nilai) == nilai


# --------------------------------------------------------------------- lapis 2: pass 1 rusak
def test_hasil_kosong_tetap_lolos_validasi():
    kosong = OllamaExtractor._empty_result(ContractExtractionSchema)
    assert kosong.pihak_pertama is not None
    assert kosong.nomor_kontrak is None


def test_json_pass1_terpotong_tidak_menggagalkan_dokumen(monkeypatch):
    ex = OllamaExtractor()
    monkeypatch.setattr(
        ex,
        "_chat",
        lambda messages, fmt=None: (
            TERPOTONG if isinstance(fmt, dict) else '{"Nomor Kontrak Kerja": "ST-108"}'
        ),
    )
    hasil = ex._run_extraction(
        "isi kontrak",
        "kontrak/SPK",
        "sistem",
        ContractExtractionSchema,
        "{null_fields}",
        lambda *a: {},
        targeted_min_chars=10**9,
    )
    assert isinstance(hasil, ContractExtractionSchema)
    assert hasil.nomor_kontrak == "ST-108"  # diisi retry per field setelah pass 1 gagal
