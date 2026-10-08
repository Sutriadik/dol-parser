"""
Tes pencocokan & normalisasi identifier (nomor dokumen, rekening, NPWP).

Kenapa lapisan ini dites terpisah: nomor kontrak adalah field paling kritis di dokumen
pengadaan — salah satu karakter membuat BAST ditolak dan tagihan tertahan — sekaligus yang
paling sering dirusak OCR. Dua kegagalan nyata yang memicu kode ini, dari
`eval/reports/baseline_akurasi_2026-09-25.json`:

    dokumen : K.TEL.054321/HK.810/T1R-0D000000/2026
    OCR+LLM : K. TEL054321/HK.810/T1R-0D000000/2026

`canon()` memecah keduanya jadi token berbeda ("tel 054321" vs "tel054321"), jadi nomor
kontrak gagal ditemukan di dokumennya sendiri dan tidak pernah bisa di-grounding.
"""

import pytest

from app.evidence.matcher import (
    calculate_match_confidence,
    identifier_skeleton,
    looks_like_identifier,
)
from app.extractors.deterministic.identifiers import (
    looks_like_document_number,
    normalize_document_number,
)


# --------------------------------------------------------------------- skeleton
@pytest.mark.parametrize(
    "a,b",
    [
        ("K.TEL.054321/HK.810/T1R-0D000000/2026", "K. TEL.054321/HK.810/T1R-0D000000/2026"),
        ("77/00/HK-08/BUT/2025", "77 / 00 / HK-08 / BUT / 2025"),
        ("123-00-4567890-1", "123.00.4567890.1"),
    ],
)
def test_skeleton_menyamakan_perbedaan_pemisah(a, b):
    assert identifier_skeleton(a) == identifier_skeleton(b)


def test_skeleton_tidak_menyamakan_nomor_yang_memang_berbeda():
    """Toleransi pemisah tidak boleh sampai menyamakan dua kontrak yang berlainan."""
    assert identifier_skeleton("77/00/HK-08/BUT/2025") != identifier_skeleton(
        "77/00/HK-08/BUT/2026"
    )
    assert identifier_skeleton("K.TEL.054321/2026") != identifier_skeleton("K.TEL.054322/2026")


def test_pelipatan_ocr_hanya_aktif_bila_diminta():
    """O/0 disamakan hanya saat fold_ocr=True; mode biasa harus tetap membedakannya."""
    a, b = "T1R-0D000000", "T1R-OD000000"
    assert identifier_skeleton(a) != identifier_skeleton(b)
    assert identifier_skeleton(a, fold_ocr=True) == identifier_skeleton(b, fold_ocr=True)


# --------------------------------------------------------------------- deteksi bentuk
@pytest.mark.parametrize(
    "teks",
    [
        "K.TEL.054321/HK.810/T1R-0D000000/2026",
        "77/00/HK-08/BUT/2025",
        "123-00-4567890-1",
        "045/SPK/LOG-02/2031",
    ],
)
def test_dikenali_sebagai_identifier(teks):
    assert looks_like_identifier(teks)
    assert looks_like_document_number(teks)


@pytest.mark.parametrize(
    "teks",
    [
        "Direktur Operasional",  # jabatan
        "Pengadaan Perpanjangan Lisensi Basis Data Tahun 2026",  # nama pekerjaan
        "Bandung",
        "",
    ],
)
def test_kalimat_biasa_bukan_identifier(teks):
    """Kalau kalimat ikut dianggap identifier, pencocokan teks normal jadi rusak."""
    assert not looks_like_identifier(teks)
    assert not looks_like_document_number(teks)


# --------------------------------------------------------------------- normalisasi nilai
@pytest.mark.parametrize(
    "masuk,harap",
    [
        ("K. TEL.054321/HK.810/T1R-0D000000/2026", "K.TEL.054321/HK.810/T1R-0D000000/2026"),
        ("765 /SPH- BUT/ 2026", "765/SPH-BUT/2026"),
        ("HK .810", "HK.810"),
    ],
)
def test_spasi_sisipan_ocr_dibuang(masuk, harap):
    assert normalize_document_number(masuk) == harap


@pytest.mark.parametrize(
    "teks",
    [
        "77/00/HK-08/BUT/2025",  # sudah bersih
        "123-00-4567890-1",
        "Nomor 45 tahun 2026",  # spasi TIDAK menempel pemisah -> jangan disentuh
    ],
)
def test_nomor_yang_sudah_benar_tidak_diubah(teks):
    assert normalize_document_number(teks) == teks


def test_nilai_bukan_nomor_dikembalikan_apa_adanya():
    for v in (None, 12345, "", "Pengadaan Lisensi Oracle untuk Universitas Contoh Tahun 2026"):
        assert normalize_document_number(v) == v


def test_normalisasi_tidak_pernah_menukar_karakter_mirip():
    """
    Menyamakan O dan 0 saat MEMBANDINGKAN itu toleransi; menukarnya di dalam NILAI berarti
    mengarang isi dokumen. Nilai yang disimpan harus apa adanya.
    """
    assert "OD000000" in normalize_document_number("K. TEL.012345/HK.810/T1R-OD000000/2025")


# --------------------------------------------------------------------- grounding
def test_nomor_dengan_spasi_ocr_tetap_ditemukan_di_dokumen():
    """Inti perbaikannya: nomor yang dulu gagal grounding kini ketemu dengan bukti kuat."""
    skor, jenis = calculate_match_confidence(
        "K. TEL054321/HK.810/T1R-0D000000/2026",
        "Nomor Tanggal K. TEL.054321/HK.810/T1R-0D000000/2026 3 Maret 2026",
    )
    assert jenis == "identifier_match"
    assert skor >= 0.85


def test_karakter_tertukar_ocr_ketemu_tapi_skornya_lebih_rendah():
    """
    O/0 tertukar masih ditautkan ke buktinya, tapi dengan jenis match yang lebih lemah --
    `STRONG_MATCH_TYPES` tidak memuatnya, jadi field-nya tidak pernah jadi AUTO_VERIFIED.
    """
    from app.evidence.locator import STRONG_MATCH_TYPES

    skor, jenis = calculate_match_confidence(
        "K.TEL.012345/HK.810/T1R-0D000000/2025",
        "Nomor : K. TEL.012345/HK.810/T1R-OD000000/2025",
    )
    assert jenis == "identifier_match_ocr_tolerant"
    assert jenis not in STRONG_MATCH_TYPES

    skor_persis, _ = calculate_match_confidence(
        "K.TEL.012345/HK.810/T1R-0D000000/2025",
        "Nomor : K. TEL.012345/HK.810/T1R-0D000000/2025",
    )
    assert skor_persis > skor, "match persis harus menang atas match toleran"


def test_nomor_berbeda_tidak_dianggap_cocok():
    """Pengaman terpenting: kontrak lain tidak boleh jadi bukti untuk nomor ini."""
    skor, jenis = calculate_match_confidence(
        "77/00/HK-08/BUT/2025",
        "Nomor 12/99/XX-01/LAIN/2019 tanggal 3 Maret",
    )
    assert jenis is None and skor == 0.0


def test_identifier_match_diakui_sebagai_bukti_kuat():
    from app.evidence.locator import STRONG_MATCH_TYPES

    assert "identifier_match" in STRONG_MATCH_TYPES


# --------------------------------------------------------------------- status UNSUPPORTED
def _ir_sederhana(potongan):
    """DocumentIR satu halaman dari daftar teks blok (lewat adapter, seperti jalur nyata)."""
    from app.document_ir.adapter import from_parsed_response

    return from_parsed_response(
        {
            "markdown": "\n".join(potongan),
            "metadata": {
                "job_id": "t",
                "page_count": 1,
                "output_markdown_chars": 1,
                "parser_engine": "test",
            },
            "structure": {
                "children": [
                    {
                        "type": "page",
                        "grounding": {
                            "page": 1,
                            "range": {"start": 0, "end": 1},
                            "box": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                        },
                        "children": [
                            {
                                "type": "paragraph",
                                "id": f"b{i}",
                                "text": t,
                                "grounding": {
                                    "page": 1,
                                    "range": {"start": 0, "end": 0},
                                    "box": {
                                        "xmin": 0.1,
                                        "ymin": 0.1 + i * 0.05,
                                        "xmax": 0.9,
                                        "ymax": 0.14 + i * 0.05,
                                    },
                                    "confidence": 0.95,
                                },
                            }
                            for i, t in enumerate(potongan)
                        ],
                    }
                ]
            },
        },
        "uji.pdf",
    )


def test_nilai_yang_tidak_ada_di_dokumen_ditandai_unsupported():
    """
    Inti "check & recheck": nilai terisi yang tidak ketemu di satu blok pun adalah
    kandidat karangan model terkuat, dan harus bisa dibedakan dari "bukti lemah".
    """
    from app.evidence.locator import build_field_evidence
    from app.schemas.evidence import FieldStatus

    ir = _ir_sederhana(["Nomor : 045/SPK/2031", "Pekerjaan Pengadaan Switch Access"])
    bukti = {
        e.field: e
        for e in build_field_evidence(
            {"Nomor Kontrak Kerja": "045/SPK/2031", "Nama Pekerjaan": "Pengadaan Gedung Baru"}, ir
        )
    }

    assert bukti["Nomor Kontrak Kerja"].status in (
        FieldStatus.AUTO_VERIFIED,
        FieldStatus.AUTO_ACCEPTED,
    )
    assert bukti["Nama Pekerjaan"].status == FieldStatus.UNSUPPORTED
    assert bukti["Nama Pekerjaan"].evidence_text is None


def test_unsupported_tetap_masuk_daftar_review_pm():
    """
    Regresi yang mudah terjadi: memecah UNSUPPORTED dari REVIEW_REQUIRED bisa membuat
    field tanpa bukti justru HILANG dari daftar yang harus dilihat PM.
    """
    from app.evidence.locator import build_field_evidence
    from app.services.engine import OpenADEEngine
    from app.validation.rules import validate_extraction

    ir = _ir_sederhana(["Nomor : 045/SPK/2031"])
    data = {"Nomor Kontrak Kerja": "045/SPK/2031", "Nama Pekerjaan": "Tidak Ada Di Dokumen"}
    bukti = build_field_evidence(data, ir, validate_extraction("contract", data))
    laporan = OpenADEEngine.build_quality_report(bukti, validate_extraction("contract", data))

    assert "Nama Pekerjaan" in laporan["unsupported_fields"]
    assert "Nama Pekerjaan" in laporan["review_required_fields"]


# --------------------------------------------------------------------- peran dua nomor kontrak
from app.extractors.deterministic.identifiers import fix_contract_number_roles  # noqa: E402

TELKOM, BUT = "PT TELEKOMUNIKASI INDONESIA Tbk", "PT BHAKTI UNGGUL TEKNOVASI"
NO_TELKOM, NO_BUT = "K.TEL.012345/HK.810/T1R-0D000000/2025", "77/00/HK-08/BUT/2025"


def test_dua_nomor_kontrak_tertukar_dikembalikan_ke_peran_benar():
    """
    Kasus nyata dari sebuah kontrak layanan: model memasang nomor BUT sebagai nomor kontrak
    utama dan nomor Telkom sebagai nomor internal — terbalik. Penentunya ada di nomornya
    sendiri: penyedia menyelipkan akronim perusahaannya ("BUT").
    """
    assert fix_contract_number_roles(NO_BUT, NO_TELKOM, TELKOM, BUT) == (NO_TELKOM, NO_BUT)


def test_urutan_yang_sudah_benar_tidak_diubah():
    """Idempoten: menjalankannya dua kali tidak boleh menukar balik."""
    sekali = fix_contract_number_roles(NO_TELKOM, NO_BUT, TELKOM, BUT)
    assert sekali == (NO_TELKOM, NO_BUT)
    assert fix_contract_number_roles(*sekali, TELKOM, BUT) == (NO_TELKOM, NO_BUT)


@pytest.mark.parametrize(
    "a,b,alasan",
    [
        ("001/A/2025", "002/B/2025", "tidak ada penanda perusahaan di nomor mana pun"),
        ("K.TEL.01/2025", "K.TEL.02/2025", "dua-duanya menunjuk pihak yang sama"),
        (NO_BUT, None, "hanya ada satu nomor"),
        (None, NO_TELKOM, "hanya ada satu nomor"),
    ],
)
def test_tidak_menukar_saat_sinyalnya_ambigu(a, b, alasan):
    """
    Nomor kontrak terlalu penting untuk ditukar berdasarkan tebakan: yang salah masih
    tertangkap konfirmasi per-field PM, yang tertukar diam-diam karena tebakan kita tidak.
    """
    assert fix_contract_number_roles(a, b, TELKOM, BUT) == (a, b), alasan


def test_tidak_menukar_saat_penanda_kedua_pihak_bertabrakan():
    """Dua anak perusahaan dengan akronim sama -> penanda tidak membedakan apa pun."""
    sama = "PT TELKOM AKSES"
    assert fix_contract_number_roles(NO_BUT, NO_TELKOM, sama, "PT TELKOM SIGMA") == (
        NO_BUT,
        NO_TELKOM,
    )


def test_koreksi_peran_terpasang_di_jalur_ekstraksi():
    """Fungsi murni tidak berguna kalau tidak dipanggil; ini menjaga sambungannya."""
    import inspect

    from app.extractors import ollama_client

    assert "fix_contract_number_roles(" in inspect.getsource(
        ollama_client.OllamaExtractor.extract_contract
    )
