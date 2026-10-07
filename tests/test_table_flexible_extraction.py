"""
Unit Tests untuk Ekstraksi Tabel Fleksibel & Redaksi Lengkap (Open ADE)
"""

from app.extractors.ollama_client import OllamaExtractor
from app.parsers.table_extractor import (
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
            "Harga Satuan": 141500.0,
            "Total Harga": 57732000.0,
            "Keterangan": "Termasuk SLA 99.5%",
            "Atribut Tambahan": {"Jumlah Titik": 34, "OTC": 0, "MRC": 57732000},
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
            "Harga Satuan": 11700000.0,
            "Jumlah Harga": 140400000.0,
            "Keterangan": "Dedicated onsite",
            "Atribut Tambahan": {"MRC": 11700000, "OTC": 0},
        }
    )
    assert item.kategori == "A. Tenaga Ahli"
    assert "Pengawasan Mutu" in item.deskripsi
    assert item.volume == 3.0
    assert item.unit == "Orang.Bulan"
    assert item.periode == "12 Bulan"
    assert item.extra_attributes["MRC"] == 11700000


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
