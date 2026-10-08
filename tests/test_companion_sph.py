"""Pemeta SPH -> baris tabel companion."""

from app.companion.sph_mapper import map_sph
from dol_schema import validate_payload


def _extract_sph():
    """Bentuk hasil ekstraksi SPH, memakai alias di app/schemas/sph.py."""
    return {
        "document_type": "sph",
        "document_name": "SPH_Contoh.pdf",
        "markdown": "# SURAT PENAWARAN HARGA",
        "run_info": {
            "document_id": "doc-sph-contoh",
            "page_count": 3,
            "parser_engine": "docling",
            "model": "qwen2.5:7b",
        },
        "validation": {"status": "ok"},
        "data": {
            "Vendor": {"Nama Vendor": "PT Vendor Contoh"},
            "Tujuan Surat / Klien": "PT Klien Contoh",
            "Nomor SPH": "SPH/CONTOH/2026/010",
            "Tanggal SPH": "12 Januari 2026",
            "Perihal / Nama Pekerjaan": "Penawaran Perangkat Jaringan",
            "Masa Berlaku Penawaran": "30 hari",
            "Mekanisme Skema Pembayaran": "100% setelah barang diterima",
            "Subtotal": 100000000,
            "Persentase PPN": "11%",
            "Nilai PPN": 11000000,
            "Grand Total": 111000000,
            "Daftar Penawaran Harga": [
                {
                    "No": "1",
                    "Nama Barang/Jasa": "Switch 24 Port",
                    "Brand/Merek": "Cisco",
                    "Part Number": "C1000-24T",
                    "Volume / Qty": 4,
                    "Satuan": "unit",
                    "Harga Satuan": 15000000,
                    "Total Harga": 60000000,
                },
                {
                    "No": "2",
                    "Nama Barang/Jasa": "Instalasi & Konfigurasi",
                    "Volume / Qty": 1,
                    "Satuan": "paket",
                    "Harga Satuan": 40000000,
                    "Total Harga": 40000000,
                },
            ],
        },
        "evidence": [
            {
                "field": "Grand Total",
                "value": "Rp 111.000.000",
                "page": 2,
                "evidence_text": "Grand Total Rp111.000.000,-",
                "evidence_score": 0.93,
                "status": "AUTO_VERIFIED",
            },
        ],
    }


def test_header_sph_terpetakan():
    p = map_sph(_extract_sph())
    sph = p["sph"][0]
    assert sph["sph_number"] == "SPH/CONTOH/2026/010"
    assert sph["sph_date"] == "2026-01-12"
    assert sph["total_price"] == 111000000
    assert sph["subtotal_value"] == 100000000
    assert sph["vat_percentage"] == "11%"
    assert sph["validity_text"] == "30 hari"


def test_vendor_dan_klien_tidak_tertukar():
    """SPH datang DARI vendor KE kita. Tertukar di sini membuat tabel banding
    antar-vendor membandingkan pihak yang salah."""
    sph = map_sph(_extract_sph())["sph"][0]
    assert sph["vendor_name"] == "PT Vendor Contoh"
    assert sph["client_name"] == "PT Klien Contoh"


def test_item_sph_terurut_dan_lengkap():
    items = map_sph(_extract_sph())["sph_item"]
    assert [i["line_no"] for i in items] == [1, 2]
    assert items[0]["description"] == "Switch 24 Port"
    assert items[0]["brand"] == "Cisco"
    assert items[0]["part_number"] == "C1000-24T"
    assert items[0]["quantity"] == 4
    assert items[1]["unit"] == "paket"
    assert all(i["_sph_ref"] == items[0]["_sph_ref"] for i in items)


def test_baris_tanpa_uraian_dibuang():
    """Baris hantu dari OCR tabel tidak boleh jadi baris penawaran."""
    e = _extract_sph()
    e["data"]["Daftar Penawaran Harga"].append({"No": "3", "Volume / Qty": 1})
    assert len(map_sph(e)["sph_item"]) == 2


def test_confidence_score_ikut_terpetakan():
    fields = map_sph(_extract_sph())["extracted_field"]
    f = next(x for x in fields if x["field_path"] == "Grand Total")
    assert f["evidence_score"] == 0.93
    assert f["system_status"] == "bukti_kuat"


def test_payload_sph_lolos_validasi_skema():
    assert validate_payload(map_sph(_extract_sph())) == []


def test_dokumen_sama_menghasilkan_kunci_sama():
    """Idempotensi: proses ulang dokumen yang sama = UPDATE, bukan baris kembar."""
    a = map_sph(_extract_sph())["document"][0]["content_hash"]
    b = map_sph(_extract_sph())["document"][0]["content_hash"]
    assert a == b


def test_kolom_milik_n8n_tidak_dikirim():
    for rows in map_sph(_extract_sph()).values():
        for r in rows:
            assert not any(k.startswith("mybhakti_") for k in r)


def test_harga_format_inggris_dari_vendor_terbaca():
    """SPH vendor sering memakai koma ribuan ("519,600,000"); pemeta lama membacanya None."""
    e = _extract_sph()
    e["data"]["Grand Total"] = "519,600,000"
    assert map_sph(e)["sph"][0]["total_price"] == 519600000.0


# Lihat penjelasan NASIB_KONTRAK di test_companion_contract.py.
NASIB_SPH = {
    "Vendor.Nama Vendor": "kolom",
    "Vendor.Alamat Vendor": "hasil_ekstraksi",
    "Vendor.Kontak / Email": "hasil_ekstraksi",
    "Vendor.NPWP": "tidak_disimpan",  # FIELD_TIDAK_DISIMPAN
    "Tujuan Surat / Klien": "kolom",
    "Nomor SPH": "kolom",
    "Tanggal SPH": "kolom",
    "Perihal / Nama Pekerjaan": "kolom",
    "Masa Berlaku Penawaran": "kolom",
    "Berlaku Sampai Tanggal": "hasil_ekstraksi",
    "Jangka Waktu Pengiriman": "hasil_ekstraksi",
    "Lokasi Pekerjaan": "hasil_ekstraksi",
    "Daftar Penawaran Harga[].No": "tidak_disimpan",  # SKIP_KEYS; line_no = urutan baca
    "Daftar Penawaran Harga[].Kategori/Kelompok": "kolom",
    "Daftar Penawaran Harga[].Nama Barang/Jasa": "kolom",
    "Daftar Penawaran Harga[].Spesifikasi": "kolom",
    "Daftar Penawaran Harga[].Brand/Merek": "kolom",
    "Daftar Penawaran Harga[].Part Number": "kolom",
    "Daftar Penawaran Harga[].Volume / Qty": "kolom",
    "Daftar Penawaran Harga[].Satuan": "kolom",
    "Daftar Penawaran Harga[].Periode/Durasi": "kolom",
    "Daftar Penawaran Harga[].Harga Satuan": "kolom",
    "Daftar Penawaran Harga[].Total Harga": "kolom",
    "Daftar Penawaran Harga[].Jenis Biaya": "kolom",  # 2026.10.4
    "Daftar Penawaran Harga[].Keterangan": "kolom",
    "Daftar Penawaran Harga[].Atribut Tambahan": "tidak_disimpan",  # SKIP_KEYS
    "Subtotal": "kolom",
    "Persentase PPN": "kolom",
    "Nilai PPN": "kolom",
    "Grand Total": "kolom",
    "Jumlah Terbilang": "hasil_ekstraksi",
    "Mekanisme Skema Pembayaran": "kolom",
    "Garansi / SLA": "hasil_ekstraksi",
    "Catatan Khusus": "hasil_ekstraksi",
    "Syarat dan Ketentuan[]": "hasil_ekstraksi",
    "Daftar Tabel Terstruktur[]": "tidak_disimpan",  # SKIP_KEYS
}


def test_setiap_field_sph_punya_nasib_tercatat():
    from app.schemas.sph import SPHExtractionSchema
    from tests.test_companion_contract import _bandingkan_nasib, nasib_sebenarnya

    dasar = {"Vendor": {}, "Daftar Penawaran Harga": [{"Nama Barang/Jasa": "Barang dasar"}]}
    sebenarnya = nasib_sebenarnya(SPHExtractionSchema, map_sph, dasar, "sph")
    _bandingkan_nasib(sebenarnya, NASIB_SPH)
