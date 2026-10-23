"""
Konsistensi dua pihak kontrak.

Kasus nyata (Nota Pesanan hasil scan): blok tanda tangan dua kolom diratakan OCR, lalu LLM
memasang penandatangan Pihak Kedua (namanya rusak dibaca OCR) ke Pihak Pertama. Jabatannya
ikut tertukar dan sempat lolos sebagai bukti_cukup. Dua pihak kontrak adalah badan berbeda
dengan wakil berbeda; nama mirip + jabatan sama hampir pasti tanda peran tertukar.

Nilai di sini rekaan.
"""

from types import SimpleNamespace

from app.extractors.ollama_client import OllamaExtractor
from app.schemas.contract import ContractExtractionSchema
from app.validation.rules import validate_contract


def _kontrak(pp_rep, pp_jab, pk_rep, pk_jab):
    return {
        "Nomor Kontrak Kerja": "12/AB/2031",
        "Nama Pekerjaan": "Pengadaan Lisensi",
        "Pihak Pertama": {
            "Nama Perusahaan": "PT Satu",
            "Nama Representative": pp_rep,
            "Jabatan": pp_jab,
        },
        "Pihak Kedua": {
            "Nama Perusahaan": "PT Dua",
            "Nama Representative": pk_rep,
            "Jabatan": pk_jab,
        },
    }


def _isu_wakil(data):
    return [i for i in validate_contract(data).issues if i.rule == "parties_distinct"]


def test_wakil_mirip_dengan_jabatan_sama_ditandai():
    isu = _isu_wakil(_kontrak("BUDIA", "DIREKTUR", "Budi Santoso", "Direktur"))
    assert len(isu) == 1
    assert "Pihak Pertama.Nama Representative" in isu[0].fields
    assert "Pihak Kedua.Nama Representative" in isu[0].fields


def test_dua_direktur_berbeda_tidak_ditandai():
    assert _isu_wakil(_kontrak("Andi Wijaya", "Direktur", "Budi Santoso", "Direktur")) == []


def test_nama_mirip_jabatan_berbeda_tidak_ditandai():
    assert _isu_wakil(_kontrak("Budi Hartono", "Senior Manager", "Budi Santoso", "Direktur")) == []


_ITEM = [
    {
        "Deskripsi Item/Barang/Pekerjaan": "Lisensi",
        "volume": 1,
        "unit": "paket",
        "Harga Satuan": 1000,
        "Jumlah Harga": 1000,
    }
]


def _ekstraktor_dengan_petunjuk(petunjuk):
    ekstraktor = OllamaExtractor.__new__(OllamaExtractor)
    ekstraktor.context_analyzer = SimpleNamespace(get_entity_hints=lambda _md: petunjuk)
    return ekstraktor


def test_alamat_kosong_diisi_dari_preambule_walau_pihak_lain_tanpa_petunjuk():
    """Dulu petunjuk diabaikan seluruhnya bila salah satu pihak tidak punya petunjuk."""
    hasil = ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": {"Nama Perusahaan": "PT Satu"},
            "Pihak Kedua": {"Nama Perusahaan": "PT Dua"},
            "List Item/Barang": _ITEM,
        }
    )
    ekstraktor = _ekstraktor_dengan_petunjuk(
        {"Pihak Kedua": {"alamat": "Jl. Contoh No. 1, Bandung"}}
    )

    ekstraktor._reconcile_parties(hasil, "teks dokumen")

    assert hasil.pihak_kedua.alamat == "Jl. Contoh No. 1, Bandung"
    assert hasil.pihak_pertama.alamat in (None, "")


def test_alamat_yang_sudah_diisi_llm_tidak_ditimpa_petunjuk():
    hasil = ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": {"Nama Perusahaan": "PT Satu"},
            "Pihak Kedua": {"Nama Perusahaan": "PT Dua", "Alamat": "Jl. Dari LLM 9"},
            "List Item/Barang": _ITEM,
        }
    )
    ekstraktor = _ekstraktor_dengan_petunjuk({"Pihak Kedua": {"alamat": "Jl. Contoh No. 1"}})

    ekstraktor._reconcile_parties(hasil, "teks dokumen")

    assert hasil.pihak_kedua.alamat == "Jl. Dari LLM 9"


# Format surat: "Kepada Yth, <jabatan> <perusahaan> <alamat>". Kasus nyata: Nota Pesanan hasil
# scan tanpa preambule "selanjutnya disebut PIHAK KEDUA". Qwen sempat mengisi jabatan Pihak
# Kedua dari baris ini, lalu mengosongkannya setelah dua perubahan kecil di teks (ejaan
# "diatas" dipertahankan, "Rp.." diperbaiki). Pembacaan deterministik tidak bergantung pada itu.
from app.services.context_analyzer import ContextAnalyzer  # noqa: E402

SURAT = (
    "# NOTA PESANAN\n\nMedan, 3 Maret 2031\n"
    "Kepada Yth, Direktur PT Contoh Jaya Jl. Mawar No. 1, Sukamaju, Kota Contoh 40000\n"
    "Telkom\n"
)


def test_alamat_surat_dibaca_jabatan_perusahaan_dan_alamat():
    assert ContextAnalyzer.parse_alamat_surat(SURAT) == {
        "jabatan": "Direktur",
        "nama_perusahaan": "PT Contoh Jaya",
        "alamat": "Jl. Mawar No. 1, Sukamaju, Kota Contoh 40000",
    }


def test_alamat_surat_dengan_nama_penerima():
    teks = "Kepada Yth. Bapak Budi Santoso, Direktur Utama PT Contoh Jaya Jalan Melati 2, Jakarta\n"
    assert ContextAnalyzer.parse_alamat_surat(teks) == {
        "nama_representative": "Budi Santoso",
        "jabatan": "Direktur Utama",
        "nama_perusahaan": "PT Contoh Jaya",
        "alamat": "Jalan Melati 2, Jakarta",
    }


def test_tanpa_kepada_yth_tidak_ada_petunjuk():
    assert ContextAnalyzer.parse_alamat_surat("PERJANJIAN KERJA SAMA\nantara PT A dan PT B") == {}


def _kontrak_tanpa_wakil(pk_perusahaan):
    return ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": {"Nama Perusahaan": "PT Pemesan"},
            "Pihak Kedua": {"Nama Perusahaan": pk_perusahaan},
            "List Item/Barang": _ITEM,
        }
    )


def _ekstraktor_asli_tanpa_preambule():
    ekstraktor = OllamaExtractor.__new__(OllamaExtractor)
    ekstraktor.context_analyzer = ContextAnalyzer()
    return ekstraktor


def test_penerima_surat_mengisi_pihak_kedua_yang_kosong():
    hasil = _kontrak_tanpa_wakil("PT Contoh Jaya")
    _ekstraktor_asli_tanpa_preambule()._reconcile_parties(
        hasil, SURAT, penerima_surat_pihak_kedua=True
    )

    assert hasil.pihak_kedua.jabatan == "Direktur"
    assert hasil.pihak_kedua.alamat.startswith("Jl. Mawar No. 1")
    assert hasil.pihak_pertama.alamat in (None, "")


def test_penerima_surat_perusahaan_lain_tidak_dipakai():
    """Bila LLM menaruh perusahaan lain di Pihak Kedua, alamat surat bukan milik pihak itu."""
    hasil = _kontrak_tanpa_wakil("CV Berbeda Sekali")
    _ekstraktor_asli_tanpa_preambule()._reconcile_parties(
        hasil, SURAT, penerima_surat_pihak_kedua=True
    )

    assert hasil.pihak_kedua.jabatan in (None, "")
    assert hasil.pihak_kedua.alamat in (None, "")


def test_surat_untuk_perorangan_dengan_baris_perihal_tergabung_tidak_menghasilkan_petunjuk():
    """Kasus nyata (SPK perorangan): OCR menggabungkan alamat penerima dengan baris Perihal yang
    menyebut perusahaan lain. Hasil lama: jabatan 'Jawa Barat Perihal : 2031 di'."""
    teks = (
        "Kepada Yth ; Sdr. Budi Contoh Rumah No. 3 RT/RW/001/002, Kel. Mawar, Kota Bandung, "
        "Jawa Barat Perihal : 2031 di PT Contoh Persero Dengan hormat, ketentuan sebagai berikut:\n"
    )
    assert ContextAnalyzer.parse_alamat_surat(teks) == {}


def test_penerima_surat_tidak_dipakai_bila_perusahaan_pihak_kedua_kosong():
    hasil = _kontrak_tanpa_wakil("")
    _ekstraktor_asli_tanpa_preambule()._reconcile_parties(
        hasil, SURAT, penerima_surat_pihak_kedua=True
    )
    assert hasil.pihak_kedua.jabatan in (None, "")


# Jumlah item yang tidak sama dengan subtotal harus menurunkan status setiap harga item.
# Kasus nyata (SPK scan di luar golden): tabel berkelompok dipecah jadi 5 baris dan harga item
# kedua tersalin dari item pertama. Aturan jumlah item sudah memberi peringatan, tetapi hanya
# atas nama "List Item/Barang", sehingga harga per item tetap berstatus bukti_kuat.


def test_jumlah_item_tidak_cocok_menandai_setiap_harga_item():
    from app.document_ir.models import DocumentBlock, DocumentIR, DocumentPage
    from app.evidence.locator import build_field_evidence
    from app.schemas.evidence import FieldStatus

    data = {
        "Nomor Kontrak Kerja": "12/AB/2031",
        "Nama Pekerjaan": "Lisensi",
        "Pihak Pertama": {"Nama Perusahaan": "PT Satu"},
        "Pihak Kedua": {"Nama Perusahaan": "PT Dua"},
        "List Item/Barang": [
            {"volume": 1, "Harga Satuan": 100_000_000, "Jumlah Harga": 100_000_000},
            {"volume": 1, "Harga Satuan": 100_000_000, "Jumlah Harga": 100_000_000},
        ],
        "sub total": 250_000_000,
    }
    laporan = validate_contract(data)
    teks = "Harga 100.000.000 dan 150.000.000. Subtotal 250.000.000. Nomor 12/AB/2031"
    ir = DocumentIR(
        document_id="d",
        file_name="uji.pdf",
        source_engine="uji",
        pages=[
            DocumentPage(
                page_number=1,
                blocks=[
                    DocumentBlock(
                        document_id="d",
                        block_id="b1",
                        page=1,
                        text=teks,
                        reading_order=0,
                        source_engine="uji",
                    )
                ],
            )
        ],
    )
    status = {e.field: e.status for e in build_field_evidence(data, ir, laporan)}

    assert status["List Item/Barang[1].Harga Satuan"] == FieldStatus.REVIEW_REQUIRED
    assert status["List Item/Barang[1].Jumlah Harga"] == FieldStatus.REVIEW_REQUIRED
    assert any(
        "List Item/Barang[1].Jumlah Harga" in i.fields
        for i in laporan.issues
        if i.rule == "items_sum_matches_subtotal"
    )


# Konsiderans kontrak: "Berita Acara Klarifikasi dan Negosiasi Harga <pekerjaan> tanggal 24
# Januari 2025". Kasus nyata: Tanggal Negosiasi kosong karena nama pekerjaan memisahkan kata
# "Negosiasi" dari "tanggal", sementara LLM menaruh tanggal yang sama di Nomor Dokumen.
from app.extractors.reconcile import rapikan_dokumen_pendukung  # noqa: E402


def _kontrak_dengan_pendukung(pendukung, **lain):
    return ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": {"Nama Perusahaan": "PT Pemesan"},
            "Pihak Kedua": {"Nama Perusahaan": "PT Contoh Jaya"},
            "Dokumen Pendukung": pendukung,
            **lain,
        }
    )


BA_NEGO = {
    "Nama Dokumen": "Berita Acara Klarifikasi dan Negosiasi Harga Pengadaan Contoh",
    "Nomor Dokumen": "24 Januari 2025",
    "Tanggal Dokumen": "24 Januari 2025",
}


def test_tanggal_negosiasi_diisi_dari_ba_negosiasi_di_dokumen_pendukung():
    kontrak = _kontrak_dengan_pendukung([BA_NEGO])
    rapikan_dokumen_pendukung(kontrak)
    assert kontrak.tanggal_negosiasi == "24 Januari 2025"


def test_tanggal_yang_masuk_nomor_dokumen_dikosongkan():
    kontrak = _kontrak_dengan_pendukung([BA_NEGO])
    rapikan_dokumen_pendukung(kontrak)
    assert kontrak.dokumen_pendukung[0].nomor_dokumen is None
    assert kontrak.dokumen_pendukung[0].tanggal_dokumen == "24 Januari 2025"


def test_nomor_dokumen_asli_dan_tanggal_negosiasi_llm_dibiarkan():
    pendukung = {**BA_NEGO, "Nomor Dokumen": "12/BA-NEGO/2025"}
    kontrak = _kontrak_dengan_pendukung([pendukung], **{"Tanggal Negosiasi": "20 Januari 2025"})
    rapikan_dokumen_pendukung(kontrak)
    assert kontrak.dokumen_pendukung[0].nomor_dokumen == "12/BA-NEGO/2025"
    assert kontrak.tanggal_negosiasi == "20 Januari 2025"


def test_tujuan_surat_sph_kosong_diisi_dari_kepada_yth():
    """Kasus nyata: OCR menggabungkan Perihal, Lampiran, dan Kepada Yth dalam satu baris."""
    from app.schemas.sph import SPHExtractionSchema

    sph = SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "CV Vendor Contoh"},
            "Nomor SPH": "01/SPH/2026",
            "Tanggal SPH": "1 Agustus 2026",
            "Subtotal": 1,
            "Grand Total": 1,
        }
    )
    teks = (
        "Bandung, 05 Juni 2026 No : ABC/P/1 Perihal : Penawaran Lampiran : Spesifikasi "
        "Kepada Yth : PT. Contoh Jaya Abadi\n\nDengan Surat ini kami bermaksud ...\n"
    )
    _ekstraktor_asli_tanpa_preambule()._lengkapi_tujuan_surat(sph, teks)
    assert sph.tujuan_surat == "PT. Contoh Jaya Abadi"


def test_konsiderans_yang_tersalin_ke_syarat_lampiran_bast_dibuang():
    """Kasus nyata (kontrak lisensi): enam surat konsiderans ("Surat Nomor ... perihal ...")
    tersalin utuh ke Syarat Lampiran Wajib BAST, dan akan menjadi checklist lampiran palsu."""
    surat = "Surat Nomor: 01/ABC/2026 tanggal 19 Juni 2026, perihal Permintaan Proposal Contoh"
    kontrak = _kontrak_dengan_pendukung(
        [{"Nama Dokumen": surat}],
        **{"Syarat Lampiran Wajib BAST": [surat, "Asli Berita Acara Serah Terima (BAST)"]},
    )
    rapikan_dokumen_pendukung(kontrak)
    assert kontrak.syarat_lampiran_bast == ["Asli Berita Acara Serah Terima (BAST)"]


def test_syarat_lampiran_kosong_setelah_dibersihkan_menjadi_none():
    surat = "Berita Acara Negosiasi Harga Tanggal 11 Agustus 2026"
    kontrak = _kontrak_dengan_pendukung(
        [{"Nama Dokumen": surat}], **{"Syarat Lampiran Wajib BAST": [surat]}
    )
    rapikan_dokumen_pendukung(kontrak)
    assert kontrak.syarat_lampiran_bast is None


# Salinan contoh prompt. Kasus nyata (SPH BUT ke yayasan): Tujuan Surat terisi "Kepala Bagian
# Umum Dinas Contoh Kota Semarang" -- persis contoh SPH di prompt, tidak ada di dokumen.
from app.extractors.reconcile import buang_salinan_contoh  # noqa: E402

PROMPT_CONTOH = (
    'Contoh:\nKepada Yth. Kepala Bagian Umum Dinas Contoh Kota Semarang\n"Lokasi": "Semarang"'
)


def _sph_tujuan(tujuan):
    from app.schemas.sph import SPHExtractionSchema

    return SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "PT Contoh"},
            "Nomor SPH": "01/SPH/2026",
            "Tanggal SPH": "1 Agustus 2026",
            "Tujuan Surat / Klien": tujuan,
            "Subtotal": 1,
            "Grand Total": 1,
        }
    )


def test_frasa_contoh_prompt_yang_tidak_ada_di_dokumen_dikosongkan():
    sph = _sph_tujuan("Kepala Bagian Umum Dinas Contoh Kota Semarang")
    dibuang = buang_salinan_contoh(sph, "Kepada Yth. Panitia Pengadaan Yayasan Lain", PROMPT_CONTOH)
    assert sph.tujuan_surat is None
    assert dibuang == ["Tujuan Surat / Klien"]


def test_frasa_yang_juga_ada_di_dokumen_dibiarkan():
    sph = _sph_tujuan("Kepala Bagian Umum Dinas Contoh Kota Semarang")
    teks = "Kepada Yth. Kepala Bagian Umum Dinas Contoh Kota Semarang"
    assert buang_salinan_contoh(sph, teks, PROMPT_CONTOH) == []
    assert sph.tujuan_surat == "Kepala Bagian Umum Dinas Contoh Kota Semarang"


def test_nilai_pendek_yang_kebetulan_sama_dengan_contoh_dibiarkan():
    """'Semarang', '12 Bulan', '14 hari kalender' bisa memang benar walau ditulis lain."""
    sph = _sph_tujuan("Semarang")
    assert buang_salinan_contoh(sph, "Dokumen lain", PROMPT_CONTOH) == []


def test_penjaga_salinan_contoh_terpasang_di_ketiga_jenis_dokumen():
    import inspect

    sumber = "".join(
        inspect.getsource(m)
        for m in (
            OllamaExtractor.extract_contract,
            OllamaExtractor.extract_sph,
            OllamaExtractor.extract_bast,
        )
    )
    assert sumber.count("buang_salinan_contoh(") == 3
