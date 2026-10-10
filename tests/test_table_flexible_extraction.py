"""
Unit Tests untuk Ekstraksi Tabel Fleksibel & Redaksi Lengkap (Open ADE)
"""

from app.extractors.ollama_client import OllamaExtractor
from app.parsers.table_extractor import (
    detect_item_columns,
    extract_item_groups_from_markdown_tables,
    extract_tables_from_markdown,
    is_markdown_table_separator,
    parse_markdown_table_row,
)
from app.schemas.contract import ItemBarangPekerjaan
from app.schemas.sph import SPHItemDetail


def test_is_markdown_table_separator():
    assert is_markdown_table_separator("|---|---|---|") is True
    assert is_markdown_table_separator("| :--- | :---: | ---: |") is True
    assert is_markdown_table_separator("| No | Uraian | Harga |") is False
    assert is_markdown_table_separator("Bukan tabel") is False


def test_parse_markdown_table_row():
    row = (
        "| 1 | Sewa Router Cisco 1941: -Pemantauan Sistem | 34 | 12 | Unit/Bulan | 900.000 | "
        "367.200.000 |"
    )
    cells = parse_markdown_table_row(row)
    assert len(cells) == 7
    assert cells[0] == "1"
    assert "Sewa Router Cisco 1941: -Pemantauan Sistem" in cells[1]
    assert cells[4] == "Unit/Bulan"


def test_extract_tables_from_markdown_with_categories():
    sample_md = """
Berikut lampiran harga penawaran:

| No | Uraian Pekerjaan | Volume | Satuan | Harga Satuan | Total Harga |
|---|---|---|---|---|---|
| A | Penyediaan Router Manage Service di 34 Lokasi | | | | |
| 1 | Sewa Router Cisco 1941 | 12 | Unit/Bulan | 900.000 | 367.200.000 |
| 2 | Jasa Manage Service: -Monitoring -Troubleshooting | 12 | Unit/Bulan | 150.000 | 61.200.000 |
| B | Sharing Knowledge | | | | |
| 1 | Sewa Perlengkapan Full Board Meeting | 1 | Paket | 20.000.000 | 60.000.000 |
| Total | Total | | | | 488.400.000 |

Demikian penawaran disampaikan.
"""
    tables = extract_tables_from_markdown(sample_md)
    assert len(tables) == 1
    table = tables[0]
    assert table["table_index"] == 1
    assert "Uraian Pekerjaan" in table["headers"]
    assert table["row_count"] >= 5

    # Check category detection
    rows = table["rows"]
    row_router = next(r for r in rows if "Sewa Router Cisco 1941" in r["Uraian Pekerjaan"])
    assert "_kategori_terdeteksi" in row_router
    assert "Penyediaan Router" in row_router["_kategori_terdeteksi"]


def test_sph_item_detail_flexible_schema():
    item = SPHItemDetail(
        **{
            "No": "1",
            "Kategori/Kelompok": "A. Penyediaan Router Manage Service",
            "Nama Barang/Jasa": (
                "Jasa Manage Service Router Cisco 1941: -Pemantauan Sistem -Penanganan Insiden "
                "-Installasi & Konfigurasi -Pemeliharaan Rutin"
            ),
            "Spesifikasi": "Cisco 1941 Enterprise Router",
            "Brand/Merek": "Cisco",
            "Part Number": "Cisco 1941/K9",
            "Volume / Qty": 12.0,
            "Satuan": "Unit/Bulan",
            "Periode/Durasi": "12 Bulan",
            "Harga Satuan": 152500.0,
            "Total Harga": 62220000.0,
            "Keterangan": "Termasuk SLA 99.5%",
            "Atribut Tambahan": {"Jumlah Titik": 34, "OTC": 0, "MRC": 62220000},
        }
    )
    assert item.kategori == "A. Penyediaan Router Manage Service"
    assert "Pemantauan Sistem" in item.nama_item
    assert item.volume == 12.0
    assert item.satuan == "Unit/Bulan"
    assert item.periode == "12 Bulan"
    assert item.extra_attributes["Jumlah Titik"] == 34


def test_contract_item_flexible_schema():
    item = ItemBarangPekerjaan(
        **{
            "Nomor Item": "1",
            "Kategori/Kelompok": "A. Tenaga Ahli",
            "Deskripsi Item/Barang/Pekerjaan": (
                "Ahli Utama Pimpro - Pemantauan Sistem, Pengawasan Mutu, & Koordinasi Stakeholder"
            ),
            "Spesifikasi": "Sertifikasi PMP / ISO 27001 Lead Implementer",
            "volume": 3.0,
            "unit": "Orang.Bulan",
            "Periode/Durasi": "12 Bulan",
            "Harga Satuan": 10850000.0,
            "Jumlah Harga": 130200000.0,
            "Keterangan": "Dedicated onsite",
            "Atribut Tambahan": {"MRC": 10850000, "OTC": 0},
        }
    )
    assert item.kategori == "A. Tenaga Ahli"
    assert "Pengawasan Mutu" in item.deskripsi
    assert item.volume == 3.0
    assert item.unit == "Orang.Bulan"
    assert item.periode == "12 Bulan"
    assert item.extra_attributes["MRC"] == 10850000


def test_ollama_client_prepare_effective_text_preserves_tables():
    extractor = OllamaExtractor()

    # Generate long document with middle table
    long_preamble = "Kop Surat & Perjanjian Kerja...\n" * 50
    table_lines = [
        "| No | Deskripsi | Volume | Satuan | Harga Satuan | Total |",
        "|---|---|---|---|---|---|",
        "| 1 | Sewa Lisensi Firewall Fortinet 100F | 2 | Unit | 85000000 | 170000000 |",
        "| 2 | Jasa Instalasi & Konfigurasi HA Pair | 1 | Lot | 15000000 | 15000000 |",
    ]
    long_middle = "Pasal 1 Ketentuan Umum...\n" * 200
    payment_clause = "Pasal 5 CARA PEMBAYARAN: Transfer ke Bank Mandiri No 12345 a.n PT Contoh\n"
    ending = "Dibuat di Bandung, Tanggal 1 Januari 2026\nDirektur Utama\n"

    full_doc = long_preamble + "\n".join(table_lines) + "\n" + long_middle + payment_clause + ending

    effective = extractor._prepare_effective_text(full_doc, max_chars=3000)

    # Verify tables are 100% preserved
    assert "Sewa Lisensi Firewall Fortinet 100F" in effective
    assert "Jasa Instalasi & Konfigurasi HA Pair" in effective
    assert "Bank Mandiri" in effective
    assert "Dibuat di Bandung" in effective


def test_extract_items_sequential_numbering():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    sample_table = """
| No. | Uraian | Volume | Satuan | Harga Satuan | Total |
|---|---|---|---|---|---|
| 1. | Item Pertama | 2 | Unit | 100.000 | 200.000 |
| 2) | Item Kedua | 1 | Pkt | 500.000 | 500.000 |
| - | Item Ketiga Tanpa Nomor | 3 | Lot | 50.000 | 150.000 |
"""
    contract_items = extract_items_from_markdown_tables(sample_table, doc_type="contract")
    assert len(contract_items) == 3
    assert contract_items[0]["Nomor Item"] == "1"
    assert contract_items[1]["Nomor Item"] == "2"
    assert contract_items[2]["Nomor Item"] == "3"

    sph_items = extract_items_from_markdown_tables(sample_table, doc_type="sph")
    assert len(sph_items) == 3
    assert sph_items[0]["No"] == "1"
    assert sph_items[1]["No"] == "2"
    assert sph_items[2]["No"] == "3"


def test_schema_item_number_defaults_and_preservation():
    # Contract item without explicit nomor_item
    item1 = ItemBarangPekerjaan(
        **{
            "Deskripsi Item/Barang/Pekerjaan": "Layanan Server",
            "volume": 1,
            "unit": "Unit",
            "Harga Satuan": 1000000,
            "Jumlah Harga": 1000000,
        }
    )
    assert item1.nomor_item is None

    # Contract item with explicit sequential number
    item2 = ItemBarangPekerjaan(
        **{
            "Nomor Item": "2",
            "Deskripsi Item/Barang/Pekerjaan": "Layanan Storage",
            "volume": 1,
            "unit": "Unit",
            "Harga Satuan": 2000000,
            "Jumlah Harga": 2000000,
        }
    )
    assert item2.nomor_item == "2"

    # SPH item without explicit No
    sph_item1 = SPHItemDetail(
        **{
            "Nama Barang/Jasa": "Lisensi DB",
            "Volume / Qty": 1,
            "Satuan": "Unit",
            "Harga Satuan": 5000000,
            "Total Harga": 5000000,
        }
    )
    assert sph_item1.nomor is None


# OTC / MRC
# Bentuk tabel meniru kontrak layanan Telkom di korpus; nilai dan uraian rekaan.
TABEL_JUDUL_BERTINGKAT = """
| | | | | Masa | Harga Kesepakatan | Harga Kesepakatan | Harga Kesepakatan | Harga Kesepakatan |
|---|---|---|---|---|---|---|---|---|
|No|Uraian Pekerjaan|Jumlah|Satuan|Layanan|Harga Bulanan|Harga Bulanan|Harga Total|Harga Total|
| | | | | (bln) | OTC | MRC | OTC | MRC |
| A | CPE | | | | | | | |
| 1 | Lisensi Firewall Contoh | 1 | paket | 12 | | 1.000.000 | | 12.000.000 |
| 2 | Pemasangan Perangkat Contoh | 2 | unit | | 500.000 | | 1.000.000 | |
"""


def test_judul_tabel_bertingkat_digabung_dan_item_terbaca():
    """
    Tabel KL menulis judul dalam tiga baris. Dulu hanya baris pertama yang dianggap judul,
    sehingga kolom uraian bernama "Kolom_2", baris "Harga Bulanan" dan "OTC/MRC" dibaca
    sebagai data, dan seluruh item jatuh ke jalur LLM.
    """
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    items = extract_items_from_markdown_tables(TABEL_JUDUL_BERTINGKAT, doc_type="contract")
    assert [i["Deskripsi Item/Barang/Pekerjaan"] for i in items] == [
        "Lisensi Firewall Contoh",
        "Pemasangan Perangkat Contoh",
    ]
    assert items[0]["Kategori/Kelompok"] == "CPE"  # baris kategori tidak ikut jadi judul
    assert items[0]["Harga Satuan"] == 1_000_000 and items[0]["Jumlah Harga"] == 12_000_000
    assert items[1]["volume"] == 2


def test_jenis_biaya_dari_judul_kolom_tempat_angka_berada():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    items = extract_items_from_markdown_tables(TABEL_JUDUL_BERTINGKAT, doc_type="contract")
    assert [i["Jenis Biaya"] for i in items] == ["MRC", "OTC"]


def test_judul_yang_menyebut_otc_dan_mrc_sekaligus_tidak_dipakai_menebak():
    """Kontrak layanan nyata: OCR menggabungkan dua judul jadi "Harga Satuan. MRC OTC"."""
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    md = """
| Uraian Pekerjaan | Jumlah | Satuan | Harga Satuan. MRC OTC | Harga Total. MRC |
|---|---|---|---|---|
| Lisensi Contoh | 1 | Paket | 1.000.000 | 12.000.000 |
| Layanan Contoh | 1 | Paket | 500.000 | |
"""
    items = extract_items_from_markdown_tables(md, doc_type="contract")
    # Baris 1: kolom total jelas MRC. Baris 2: hanya kolom gabungan yang terisi -> kosong.
    assert [i["Jenis Biaya"] for i in items] == ["MRC", None]


def test_baris_berisi_otc_dan_mrc_ditandai_keduanya():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    md = """
| Uraian | Jumlah | Harga OTC | Harga MRC |
|---|---|---|---|
| Internet Contoh | 1 | 2.000.000 | 750.000 |
"""
    items = extract_items_from_markdown_tables(md, doc_type="sph")
    assert items[0]["Jenis Biaya"] == "OTC dan MRC"


def test_tabel_tanpa_otc_mrc_jenis_biaya_kosong():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    md = """
| No | Uraian | Volume | Satuan | Harga Satuan | Jumlah Harga |
|---|---|---|---|---|---|
| 1 | Barang Contoh | 2 | unit | 100.000 | 200.000 |
"""
    for dt in ("contract", "sph"):
        assert extract_items_from_markdown_tables(md, doc_type=dt)[0]["Jenis Biaya"] is None


# Bentuk tabel SPH jasa konsultansi: kolom "Vol" dibelah dua sub-sel (orang | bulan), kolom
# satuan tanpa judul karena Docling menggabungkan "Sat" ke judul harga, dan kolom nominal
# berjudul "Jumlah (Rp)". Nilai buatan, bukan data klien.
TABEL_VOL_TERBELAH = """
|  No  | Uraian              |  Vol |           | Harga Satuan Sat (Rp) | Jumlah (Rp) |
|:----:|:--------------------|-----:|:----------|----------------------:|------------:|
|  A   | Biaya Personel      | Biaya Personel | Biaya Personel | Biaya Personel | Biaya Personel |
|  1   | Konsultan Contoh    |  1 2 | orang/bln |             4.000.000 |   8.000.000 |
|  2   | Asisten Contoh      | 1. id:) | orang/bln |          3.000.000 |   6.000.000 |
|  B   | Non Personel        | Non Personel | Non Personel | Non Personel | Non Personel |
|  1   | Cetak Laporan Contoh |   1 | LS        |               250.000 |     250.000 |
| Jumlah PPN | Jumlah PPN | Jumlah PPN | Jumlah PPN | Jumlah PPN | 14.250.000 |
"""


def test_sel_vol_terbelah_tidak_digabung_jadi_satu_angka():
    """
    SPH jasa: sel Vol "1 | 2" (1 orang x 2 bulan) terbaca "1 2", spasinya dibuang jadi 12,
    lalu Total Harga dihitung 12 x harga satuan, 6x lipat dari Jumlah yang tertulis.
    Volume yang tidak terbaca utuh harus kosong, teks aslinya tetap terlihat.
    """
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    items = extract_items_from_markdown_tables(TABEL_VOL_TERBELAH, doc_type="sph")
    konsultan = items[0]
    assert konsultan["Nama Barang/Jasa"] == "Konsultan Contoh"
    assert konsultan["Volume / Qty"] is None
    assert konsultan["Atribut Tambahan"]["Vol"] == "1 2"
    assert konsultan["Harga Satuan"] == 4_000_000
    assert konsultan["Total Harga"] == 8_000_000


def test_sisa_ocr_di_sel_vol_tidak_dibaca_sebagai_volume():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    asisten = extract_items_from_markdown_tables(TABEL_VOL_TERBELAH, doc_type="sph")[1]
    assert asisten["Volume / Qty"] is None
    assert asisten["Atribut Tambahan"]["Vol"] == "1. id:)"
    assert asisten["Total Harga"] == 6_000_000


def test_kolom_jumlah_rp_dibaca_sebagai_total_dan_jumlahnya_sama_dengan_dokumen():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    items = extract_items_from_markdown_tables(TABEL_VOL_TERBELAH, doc_type="sph")
    assert [i["Total Harga"] for i in items] == [8_000_000, 6_000_000, 250_000]
    assert sum(i["Total Harga"] for i in items) == 14_250_000  # baris "Jumlah PPN"
    assert items[2]["Volume / Qty"] == 1
    assert all("Jumlah (Rp)" not in (i["Atribut Tambahan"] or {}) for i in items)


def test_total_yang_tidak_tertulis_tidak_dihitung_dari_volume_kali_harga():
    """Aturan 3 AGENTS.md: harga yang tidak tertulis dikirim kosong, bukan vol x harga."""
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    md = """
| No | Uraian | Volume | Satuan | Harga Satuan | Total Harga |
|---|---|---|---|---|---|
| 1 | Barang Contoh | 3 | unit | 100.000 | |
| 2 | Jasa Contoh | 2 | paket | | 500.000 |
"""
    sph = extract_items_from_markdown_tables(md, doc_type="sph")
    assert sph[0]["Harga Satuan"] == 100_000 and sph[0]["Total Harga"] is None
    assert sph[1]["Harga Satuan"] is None and sph[1]["Total Harga"] == 500_000

    kontrak = extract_items_from_markdown_tables(md, doc_type="contract")
    assert kontrak[0]["Jumlah Harga"] is None
    assert kontrak[1]["Harga Satuan"] is None
    # Item berharga kosong tetap lolos skema, tidak memaksa jalur LLM.
    SPHItemDetail.model_validate(sph[0])
    ItemBarangPekerjaan.model_validate(kontrak[1])


def test_sel_vol_berisi_satu_angka_tetap_terbaca():
    from app.parsers.table_extractor import baca_volume

    assert baca_volume("12") == 12
    assert baca_volume("10 Unit") == 10
    assert baca_volume("1.000") == 1000
    assert baca_volume("1,5") == 1.5
    assert baca_volume("2 (dua)") == 2
    assert baca_volume("1 2") is None
    assert baca_volume("12. d:") is None


def test_kolom_jumlah_di_sebelah_qty_dibaca_sebagai_total_bukan_dihitung():
    """
    SPH barang: volume punya kolom "Qty" sendiri (kosong), nominal di kolom "Jumlah". Kontrak:
    judul nominal salah OCR jadi "Jmlah Harga (Rp)". Dulu keduanya tidak dikenali dan total
    "benar" hanya kebetulan: volume bawaan 1 x harga satuan.
    """
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    sph_barang = """
| No | | Qty | Freq | Satuan | Harga Satuan | Jumlah |
|---|---|---|---|---|---|---|
| 1 | Mesin Contoh | | | Unit | 7,000,000 | 14,000,000 |
"""
    item = extract_items_from_markdown_tables(sph_barang, doc_type="sph")[0]
    assert item["Total Harga"] == 14_000_000
    assert item["Harga Satuan"] == 7_000_000

    st108 = """
| No. | Model | Vol | Sat | Harga Satuan (Rp) | Jmlah Harga (Rp) |
|---|---|---|---|---|---|
| 1 | Lisensi Contoh | 2 | Paket | 3.000.000 | 6.000.000 |
"""
    item = extract_items_from_markdown_tables(st108, doc_type="contract")[0]
    assert item["Jumlah Harga"] == 6_000_000 and item["volume"] == 2


def test_jumlah_sebagai_volume_tidak_dianggap_kolom_total():
    from app.parsers.table_extractor import detect_item_columns

    cols = detect_item_columns(["No", "Uraian", "Jumlah", "Satuan", "Harga Satuan"])
    assert cols["qty"] == "Jumlah"
    assert cols["total"] == []


# Bundel kontrak buatan: BA negosiasi (terpotong di batas halaman), rincian lengkap, dan nota
# pesanan (baris terakhir terpotong). Ketiganya salinan tabel harga yang sama; teks barisnya
# tidak identik antar-salinan, seperti di dokumen aslinya.
BUNDEL_TIGA_SALINAN = """
## Berita Acara Negosiasi

| No | Nama Pekerjaan | Qty. | Satuan | Kesepakatan. Harga Satuan | Kesepakatan. Harga Total |
|---|---|---|---|---|---|
| 1 | Pelatihan Contoh | 1 | Paket | 10.000.000 | 10.000.000 |
| 2 | Tiket Contoh | 2 | Orang | 1.000.000 | 2.000.000 |
| | Transportasi Contoh | 1 | Hari | | |

## Rincian Pekerjaan

| No | Spesifikasi Pekerjaan | Vol | Sat | Harga Sat (Rp) | Jml Harga (Rp) |
|---|---|---|---|---|---|
| 1 | Pelatihan Contoh | 1 | Paket | 10.000.000 | 10.000.000 |
| 2 | Tiket Contoh (Kota A - Kota B) | 2 | Orang | 1.000.000 | 2.000.000 |
| 3 | Transportasi Contoh | 1 | Hari | 500.000 | 500.000 |
| 4 | Dokumentasi Contoh | 1 | Paket | 250.000 | 250.000 |
| Jml Harga | Jml Harga | Jml Harga | Jml Harga | | 12.750.000 |

## Nota Pesanan

| No | Nama Pekerjaan | Qty. | Satuan | Kesepakatan. Harga Satuan | Kesepakatan. Harga Total |
|---|---|---|---|---|---|
| 1 | Pelatihan Contoh | 1 | Paket | 10.000.000 | 10.000.000 |
| 2 | Tiket Contoh | 2 | Orang | 1.000.000 | 2.000.000 |
| 3 | Transportasi Contoh | 1 | Hari | 500.000 | 500.000 |
"""


def _rekonsiliasi_kontrak(md, acuan):
    from app.extractors.reconcile import reconcile_items
    from app.parsers.table_extractor import extract_item_groups_from_markdown_tables

    kelompok = extract_item_groups_from_markdown_tables(md, doc_type="contract")
    return reconcile_items(
        [],
        [i for g in kelompok for i in g],
        ItemBarangPekerjaan,
        "deskripsi",
        "jumlah_harga",
        "Jumlah Harga",
        acuan,
        table_groups=kelompok,
        markdown_text=md,
    )


def test_tabel_harga_yang_diulang_di_bundel_kontrak_tidak_menggandakan_item():
    """
    Kontrak bundel: BA negosiasi, rincian, dan nota pesanan memuat tabel harga yang sama.
    Dulu semua tabel digabung (item ganda, dan item yang hanya ada di salinan lengkap bisa
    hilang). Yang dipakai harus satu salinan utuh yang jumlahnya sama dengan subtotal.
    """
    items = _rekonsiliasi_kontrak(BUNDEL_TIGA_SALINAN, [12_750_000, 14_152_500])
    assert [i.deskripsi for i in items] == [
        "Pelatihan Contoh",
        "Tiket Contoh (Kota A - Kota B)",
        "Transportasi Contoh",
        "Dokumentasi Contoh",
    ]
    assert sum(i.jumlah_harga for i in items) == 12_750_000


def test_tabel_bersambung_lintas_halaman_tetap_digabung():
    md = """
| No | Uraian | Volume | Satuan | Harga Satuan | Jumlah Harga |
|---|---|---|---|---|---|
| 1 | Barang Contoh A | 1 | unit | 1.000.000 | 1.000.000 |
| 2 | Barang Contoh B | 1 | unit | 2.000.000 | 2.000.000 |

<!-- PAGE BREAK -->

| No | Uraian | Volume | Satuan | Harga Satuan | Jumlah Harga |
|---|---|---|---|---|---|
| 3 | Barang Contoh C | 1 | unit | 3.000.000 | 3.000.000 |
"""
    items = _rekonsiliasi_kontrak(md, [6_000_000, None])
    assert [i.deskripsi for i in items] == ["Barang Contoh A", "Barang Contoh B", "Barang Contoh C"]


def test_tanpa_salinan_yang_cocok_subtotal_semua_tabel_tetap_dipakai():
    """Tidak ada angka tertulis yang mengonfirmasi salinan mana pun: jangan memilih-milih."""
    items = _rekonsiliasi_kontrak(BUNDEL_TIGA_SALINAN, [99_000_000, None])
    assert len(items) == 2 + 4 + 3


def test_angka_llm_yang_tidak_tertulis_tidak_memicu_pemilihan_salinan():
    """
    Rujukan pemilihan salinan adalah isian LLM. Bila subtotal itu tidak tertulis di dokumen,
    kecocokannya dengan jumlah salah satu salinan bisa kebetulan (atau hasil LLM menjumlah
    sendiri), jadi tidak boleh dipakai membuang tabel lain.
    """
    tanpa_baris_jumlah = "\n".join(
        b for b in BUNDEL_TIGA_SALINAN.splitlines() if not b.startswith("| Jml Harga")
    )
    assert "12.750.000" not in tanpa_baris_jumlah
    items = _rekonsiliasi_kontrak(tanpa_baris_jumlah, [12_750_000, None])
    assert len(items) == 2 + 4 + 3


def test_subtotal_yang_hanya_tertulis_sebagai_terbilang_tetap_jadi_rujukan():
    md = "\n".join(b for b in BUNDEL_TIGA_SALINAN.splitlines() if not b.startswith("| Jml Harga"))
    md += "\nTotal (Dua Belas Juta Tujuh Ratus Lima Puluh Ribu Rupiah).\n"
    assert "12.750.000" not in md
    items = _rekonsiliasi_kontrak(md, [12_750_000, None])
    assert len(items) == 4


# Judul kolom yang menyambung. OCR kadang membuang spasi di antara dua kata ("HargaSatuan",
# "TotalHarga"; bentuk ini ditemukan di hasil pindai nyata). Pencocokan judul bekerja per
# kata, sehingga kolom harga tidak dikenali dan nominalnya jatuh ke Atribut Tambahan.
def test_judul_kolom_yang_menyambung_tetap_dikenali_sebagai_harga():
    kolom = detect_item_columns(["No", "Uraian", "Qty", "Satuan", "HargaSatuan", "TotalHarga"])
    assert kolom["price"] == ["HargaSatuan"]
    assert kolom["total"] == ["TotalHarga"]
    assert kolom["qty"] == "Qty" and kolom["unit"] == "Satuan"


def test_singkatan_berhuruf_campur_tidak_ikut_dipecah():
    """ "UoM" harus tetap terbaca sebagai satuan, bukan dipecah menjadi "Uo M"."""
    kolom = detect_item_columns(["No", "Deskripsi", "Qty", "UoM", "JmlHarga"])
    assert kolom["unit"] == "UoM"
    assert kolom["total"] == ["JmlHarga"]


def test_item_dari_tabel_berjudul_menyambung_memuat_harga():
    md = (
        "| No | Uraian | Qty | Satuan | HargaSatuan | TotalHarga |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Switch 24 port | 2 | Unit | 4.500.000 | 9.000.000 |\n"
        "| 2 | Jasa instalasi | 1 | Paket | 1.250.000 | 1.250.000 |\n"
    )
    kelompok = extract_item_groups_from_markdown_tables(md, doc_type="sph")
    items = [SPHItemDetail.model_validate(i) for g in kelompok for i in g]
    assert [(i.harga_satuan, i.total_harga) for i in items] == [
        (4_500_000.0, 9_000_000.0),
        (1_250_000.0, 1_250_000.0),
    ]
