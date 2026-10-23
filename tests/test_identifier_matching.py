"""
Tes pencocokan & normalisasi identifier (nomor dokumen, rekening, NPWP).

Kenapa lapisan ini dites terpisah: nomor kontrak adalah field paling kritis di dokumen
pengadaan (salah satu karakter membuat BAST ditolak dan tagihan tertahan), sekaligus yang
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
    bersihkan_nama_pekerjaan,
    looks_like_document_number,
    normalize_document_number,
)


# skeleton
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


# deteksi bentuk
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


# normalisasi nilai
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


# grounding
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


# status UNSUPPORTED
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


# peran dua nomor kontrak
from app.extractors.deterministic.identifiers import fix_contract_number_roles  # noqa: E402

TELKOM, BUT = "PT TELEKOMUNIKASI INDONESIA Tbk", "PT BHAKTI UNGGUL TEKNOVASI"
NO_TELKOM, NO_BUT = "K.TEL.012345/HK.810/T1R-0D000000/2025", "77/00/HK-08/BUT/2025"


def test_dua_nomor_kontrak_tertukar_dikembalikan_ke_peran_benar():
    """
    Kasus nyata dari sebuah kontrak layanan: model memasang nomor BUT sebagai nomor kontrak
    utama dan nomor Telkom sebagai nomor internal, terbalik. Penentunya ada di nomornya
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


# nama pekerjaan
# Nama pekerjaan adalah pengikat dokumen satu proyek (kontrak, SPH, BAST). Sebutan jenis
# surat yang ikut tersalin di depannya membuat nama yang sama tampak berbeda di NocoDB.
@pytest.mark.parametrize(
    "masuk,harap",
    [
        (
            "Berita Acara Klarifikasi dan Negosiasi Penyediaan Pelatihan Perangkat IT untuk "
            "Kampus Contoh Tahun 2025",
            "Penyediaan Pelatihan Perangkat IT untuk Kampus Contoh Tahun 2025",
        ),
        (
            "Nota Pesanan Pengadaan Perangkat Switch Kebutuhan Dinas Contoh",
            "Pengadaan Perangkat Switch Kebutuhan Dinas Contoh",
        ),
        (
            "Surat Penawaran Harga (Indicative Offering) Penyediaan Router Manage Service "
            "untuk Dinas Contoh Tahun 2026",
            "Penyediaan Router Manage Service untuk Dinas Contoh Tahun 2026",
        ),
        (
            "Penawaran Harga Pelatihan Elektronika Dasar Angkatan II Tahun 2026",
            "Pelatihan Elektronika Dasar Angkatan II Tahun 2026",
        ),
        (
            "Penawaran Pengadaan Penyusunan Dokumen SDLC Sistem Contoh",
            "Pengadaan Penyusunan Dokumen SDLC Sistem Contoh",
        ),
        (
            "Permintaan Penawaran Harga Pengadaan Perpanjangan Server Aplikasi Contoh",
            "Pengadaan Perpanjangan Server Aplikasi Contoh",
        ),
        (
            "Surat Perintah Kerja Pekerjaan Pengadaan Jasa Pembuatan Video Contoh",
            "Pengadaan Jasa Pembuatan Video Contoh",
        ),
        (
            "BERITA ACARA SERAH TERIMA PENGADAAN PERANGKAT JARINGAN KAMPUS CONTOH",
            "PENGADAAN PERANGKAT JARINGAN KAMPUS CONTOH",
        ),
        (
            "Perjanjian Kerja Sama tentang Pengadaan Lisensi Aplikasi Contoh",
            "Pengadaan Lisensi Aplikasi Contoh",
        ),
        (
            "Pekerjaan Pengadaan Perangkat Simulator Contoh",
            "Pengadaan Perangkat Simulator Contoh",
        ),
        (
            "**Nota Pesanan: Penyediaan Layanan Hosting Contoh**",
            "Penyediaan Layanan Hosting Contoh",
        ),
    ],
)
def test_sebutan_jenis_surat_dibuang_dari_nama_pekerjaan(masuk, harap):
    assert bersihkan_nama_pekerjaan(masuk) == harap


@pytest.mark.parametrize(
    "teks",
    [
        "Pengadaan Perpanjangan Lisensi Aplikasi Contoh Tahun 2026",
        # "Pekerjaan" di sini bagian dari nama, bukan kata pengantar sebelum kata kerja pengadaan.
        "Pekerjaan Regional Expert Contoh",
        "Penyediaan Layanan Kontrak Payung Contoh",
    ],
)
def test_nama_pekerjaan_yang_sudah_bersih_tidak_diubah(teks):
    assert bersihkan_nama_pekerjaan(teks) == teks


@pytest.mark.parametrize("teks", ["Penawaran", "Surat Penawaran Harga", "Nota Pesanan", "  "])
def test_sebutan_surat_saja_bukan_nama_pekerjaan(teks):
    """Perihal yang hanya berbunyi 'Penawaran' tidak memuat nama proyek; jangan disimpan."""
    assert bersihkan_nama_pekerjaan(teks) is None


def test_hasil_pembersihan_tetap_potongan_teks_dokumen():
    """Hanya memotong dari depan: nilainya tetap bisa ditemukan di dokumen (grounding)."""
    asli = "Berita Acara Serah Terima Hasil Pekerjaan Pengadaan Access Point Dinas Contoh"
    assert bersihkan_nama_pekerjaan(asli) in asli


def test_pembersih_nama_pekerjaan_terpasang_di_ketiga_skema():
    from app.schemas.bast import BASTExtractionSchema
    from app.schemas.contract import ContractExtractionSchema
    from app.schemas.sph import SPHExtractionSchema

    pihak = {"Nama Perusahaan": "PT Contoh"}
    kontrak = ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": pihak,
            "Pihak Kedua": pihak,
            "Nama Pekerjaan": "Nota Pesanan Pengadaan Router Contoh",
        }
    )
    sph = SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "PT Contoh"},
            "Nomor SPH": "01/SPH/2026",
            "Tanggal SPH": "1 Juli 2026",
            "Perihal / Nama Pekerjaan": "Penawaran Harga Pengadaan Router Contoh",
            "Subtotal": 1,
            "Grand Total": 1,
        }
    )
    bast = BASTExtractionSchema.model_validate(
        {
            "Pihak Pertama": pihak,
            "Pihak Kedua": pihak,
            "Nama Pekerjaan": "Berita Acara Serah Terima Pengadaan Router Contoh",
        }
    )
    assert kontrak.nama_pekerjaan == sph.perihal == bast.nama_pekerjaan == "Pengadaan Router Contoh"


@pytest.mark.parametrize(
    "masuk,harap",
    [
        # Kasus nyata (SPH vendor scan): OCR menggabungkan baris kop "Perihal : Penawaran" dengan
        # "Lampiran : Spesifikasi"; hasil lama menyimpan "Lampiran : Spesifikasi".
        ("Penawaran Lampiran : Spesifikasi", None),
        ("Pengadaan Router Contoh Lampiran : 1 (satu) berkas", "Pengadaan Router Contoh"),
        ("Pengadaan Router Contoh Kepada Yth : PT Contoh", "Pengadaan Router Contoh"),
    ],
)
def test_label_kop_berikutnya_tidak_ikut_nama_pekerjaan(masuk, harap):
    assert bersihkan_nama_pekerjaan(masuk) == harap


# rujukan surat permintaan di SPH
# SPH membalas surat permintaan penawaran (SPPH atau undangan). Kalimat pembukanya menyebut
# nomor, tanggal, dan perihal surat itu -- sering lebih utuh daripada baris perihal di kop yang
# rusak OCR. Kasus nyata: kop terbaca "Perihal Kebutuhan Direktorat ..." (separuh pertama hilang).
from app.services.context_analyzer import ContextAnalyzer  # noqa: E402

SPH_MENUNJUK = (
    "Kepada Yth. Kepala Bagian Aset Kampus Contoh\n\n"
    "Perihal Kebutuhan Direktorat Teknologi Contoh\n\n## Dengan Hormat,\n\n"
    "Menunjuk Surat Nomor: 123/ABC01/ABC-SET/2026 tanggal 31 Juli 2026 perihal Permintaan "
    "Penawaran Harga Pengadaan Lisensi Contoh Tahun 2026 Kebutuhan Direktorat Teknologi Contoh. "
    "Kami sampaikan penawaran harga untuk pengadaan tersebut.\n"
)


def test_rujukan_surat_permintaan_dibaca_dari_kalimat_pembuka():
    assert ContextAnalyzer.parse_rujukan_permintaan(SPH_MENUNJUK) == {
        "nomor": "123/ABC01/ABC-SET/2026",
        "tanggal": "31 Juli 2026",
        "perihal": "Permintaan Penawaran Harga Pengadaan Lisensi Contoh Tahun 2026 "
        "Kebutuhan Direktorat Teknologi Contoh",
    }


def test_rujukan_template_spph_bentuk_merujuk():
    teks = (
        "Dengan Hormat,\nMerujuk Surat Permintaan Penawaran Harga Nomor: 001/22/ABC-04/XYZ/2026 "
        "tanggal 9 Agustus 2026 perihal Pengadaan Server Aplikasi Contoh. Berikut kami sampaikan\n"
    )
    r = ContextAnalyzer.parse_rujukan_permintaan(teks)
    assert r["nomor"] == "001/22/ABC-04/XYZ/2026"
    assert r["perihal"] == "Pengadaan Server Aplikasi Contoh"


def test_undangan_tanpa_perihal_tetap_memberi_nomor_dan_tanggal():
    teks = (
        "Sehubungan dengan undangan pengadaan langsung nomor : 031/07/PL/ABC /IV.02/III/2026 "
        "tanggal 09 Maret 2026, dengan ini kami mengajukan penawaran untuk pengadaan jasa.\n"
    )
    assert ContextAnalyzer.parse_rujukan_permintaan(teks) == {
        "nomor": "031/07/PL/ABC/IV.02/III/2026",
        "tanggal": "09 Maret 2026",
    }


def test_akta_pendirian_bukan_rujukan_permintaan():
    teks = "didirikan berdasarkan Akta Nomor 128 tanggal 24 September 1991 di hadapan Notaris.\n"
    assert ContextAnalyzer.parse_rujukan_permintaan(teks) == {}


def _sph_dengan_perihal(perihal):
    from app.extractors.ollama_client import OllamaExtractor
    from app.schemas.sph import SPHExtractionSchema

    ekstraktor = OllamaExtractor.__new__(OllamaExtractor)
    ekstraktor.context_analyzer = ContextAnalyzer()
    sph = SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "PT Contoh"},
            "Nomor SPH": "01/SPH/2026",
            "Tanggal SPH": "1 Agustus 2026",
            "Perihal / Nama Pekerjaan": perihal,
            "Subtotal": 1,
            "Grand Total": 1,
        }
    )
    ekstraktor._lengkapi_rujukan_permintaan(sph, SPH_MENUNJUK)
    return sph


def test_perihal_kop_terpotong_dilengkapi_dari_rujukan():
    sph = _sph_dengan_perihal("Kebutuhan Direktorat Teknologi Contoh")
    assert sph.perihal == (
        "Pengadaan Lisensi Contoh Tahun 2026 Kebutuhan Direktorat Teknologi Contoh"
    )
    assert sph.nomor_surat_permintaan == "123/ABC01/ABC-SET/2026"
    assert sph.tanggal_surat_permintaan == "31 Juli 2026"


def test_perihal_kop_yang_berbeda_tidak_ditimpa_rujukan():
    """Rujukan hanya melengkapi potongan; perihal lain tetap milik PM untuk diputuskan."""
    assert _sph_dengan_perihal("Pengadaan Router Lain").perihal == "Pengadaan Router Lain"


def test_rujukan_permintaan_tidak_dikirim_ke_llm():
    """Field ini dibaca deterministik; menambahkannya ke skema LLM mengubah masukan pass-1."""
    from app.extractors.llm_output import slim_schema
    from app.extractors.ollama_client import DETERMINISTIC_FIELDS
    from app.schemas.sph import SPHExtractionSchema

    props = slim_schema(SPHExtractionSchema, DETERMINISTIC_FIELDS)["properties"]
    assert "Nomor Surat Permintaan Dirujuk" not in props
    assert "Tanggal Surat Permintaan Dirujuk" not in props


# Tanggal yang ditulis LLM dalam format ISO tetap harus ditemukan di dokumen yang menulisnya
# "29 Juni 2026". Kasus nyata (kontrak lisensi): Tanggal Negosiasi benar tetapi berstatus
# UNSUPPORTED, karena "2026-06-29" diperlakukan sebagai angka dan tidak pernah cocok.
def test_tanggal_iso_ditemukan_dalam_tulisan_indonesia():
    teks = "Berita Acara Klarifikasi dan Negosiasi Pengadaan Contoh tanggal 29 Juni 2026."
    skor, jenis = calculate_match_confidence("2026-06-29", teks)
    assert jenis == "date_match" and skor >= 0.9


def test_tanggal_iso_berbeda_tidak_dianggap_cocok():
    teks = "Berita Acara Klarifikasi dan Negosiasi Pengadaan Contoh tanggal 29 Juni 2026."
    assert calculate_match_confidence("2026-06-30", teks) == (0.0, None)


def test_rujukan_dengan_singkatan_no():
    """Kasus nyata (SPH BUT ke yayasan): "Menunjuk Surat No : ... tanggal ... perihal ..."."""
    teks = (
        "Menunjuk Surat No : 0099/00/ABC-LG02/XYZ/2026 tanggal 19 Juni 2026 perihal Surat "
        "Permintaan Proposal Penawaran Pengadaan Lisensi Contoh Tahun 2026, bersama ini kami\n"
    )
    r = ContextAnalyzer.parse_rujukan_permintaan(teks)
    assert r["nomor"] == "0099/00/ABC-LG02/XYZ/2026"
    assert bersihkan_nama_pekerjaan(r["perihal"]).startswith("Pengadaan Lisensi Contoh Tahun 2026")


def test_perihal_kop_yang_sudah_utuh_tidak_ditimpa_perihal_rujukan_yang_lebih_panjang():
    from app.extractors.ollama_client import OllamaExtractor
    from app.schemas.sph import SPHExtractionSchema

    ekstraktor = OllamaExtractor.__new__(OllamaExtractor)
    ekstraktor.context_analyzer = ContextAnalyzer()
    sph = SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "PT Contoh"},
            "Nomor SPH": "01/SPH/2026",
            "Tanggal SPH": "1 Agustus 2026",
            "Perihal / Nama Pekerjaan": "Surat Penawaran Harga Pengadaan Lisensi Contoh",
            "Subtotal": 1,
            "Grand Total": 1,
        }
    )
    teks = (
        "Menunjuk Surat No : 0099/00/ABC/2026 tanggal 19 Juni 2026 perihal Surat Permintaan "
        "Proposal Penawaran Pengadaan Lisensi Contoh. Bersama ini kami sampaikan\n"
    )
    ekstraktor._lengkapi_rujukan_permintaan(sph, teks)
    assert sph.perihal == "Pengadaan Lisensi Contoh"


# Dokumen Indonesia menulis "30 (tiga puluh) hari kalender"; LLM menyalinnya "30 hari kalender".
# Kasus nyata (SPH layanan): masa berlaku benar tetapi berstatus UNSUPPORTED.
def test_terbilang_dalam_kurung_diabaikan_saat_mencocokkan():
    teks = "3. Masa berlaku penawaran ini selama 30 (tiga puluh) hari kalender."
    skor, jenis = calculate_match_confidence("30 hari kalender", teks)
    assert jenis is not None and skor >= 0.7


def test_angka_lain_tidak_ikut_cocok_karena_terbilang_dibuang():
    teks = "3. Masa berlaku penawaran ini selama 30 (tiga puluh) hari kalender."
    assert calculate_match_confidence("14 hari kalender", teks) == (0.0, None)


@pytest.mark.parametrize(
    "masuk,harap",
    [
        # Kasus nyata (nota pesanan): OCR menumpuk label kop di depan judul lampiran.
        (
            "Lampiran : 1 berkas : Nota Pesanan Penyediaan Layanan Contoh Tahun 2026",
            "Penyediaan Layanan Contoh Tahun 2026",
        ),
        ("Lampiran SPK : Pengadaan Lisensi Contoh", "Pengadaan Lisensi Contoh"),
        ("Perihal : Penawaran Harga Pengadaan Router Contoh", "Pengadaan Router Contoh"),
    ],
)
def test_label_kop_di_depan_nama_pekerjaan_dibuang(masuk, harap):
    assert bersihkan_nama_pekerjaan(masuk) == harap
