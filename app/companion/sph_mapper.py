"""
Open ADE — Pemeta hasil ekstraksi SPH (Surat Penawaran Harga) -> baris tabel companion.

Masukan: isi `*.extract.json` jenis SPH (lihat app/schemas/sph.py untuk nama aliasnya).
Keluaran: {nama_tabel: [baris, ...]} sesuai dol_schema.

SPH adalah dokumen HULU: penawaran dari vendor ke kita. Karena itu `vendor_name` diisi dari
blok Vendor, dan `client_name` dari tujuan surat -- bukan sebaliknya. Tertukar di sini
membuat tabel banding antar-vendor (Bulan 5) membandingkan pihak yang salah.

Kolom `mybhakti_*_ref` sengaja tidak dikirim: diisi n8n, dan mengirim None di sini akan
menghapus isian n8n setiap kali dokumen diproses ulang.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.companion.common import (
    document_key,
    document_row,
    extracted_field_rows,
    extraction_run_row,
    iso,
    jenis_biaya,
    num,
    parse_indonesian_date,
    txt,
)
from dol_schema import SCHEMA_VERSION


def map_sph(
    result: dict[str, Any], pdf_path: Path | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Hasil ekstraksi SPH -> {tabel: [baris]} sesuai dol_schema."""
    data = result.get("data") or {}
    doc_key = document_key(result, pdf_path)

    vendor = data.get("Vendor") or {}
    tanggal_teks = txt(data.get("Tanggal SPH"))
    sph = {
        "_document_ref": doc_key,
        "sph_number": txt(data.get("Nomor SPH")),
        "sph_date_text": tanggal_teks,
        "sph_date": iso(parse_indonesian_date(tanggal_teks)),
        "project_name": txt(data.get("Perihal / Nama Pekerjaan")),
        "client_name": txt(data.get("Tujuan Surat / Klien")),
        "vendor_name": txt(vendor.get("Nama Vendor")),
        "subtotal_value": num(data.get("Subtotal")),
        "vat_percentage": txt(data.get("Persentase PPN")),
        "vat_value": num(data.get("Nilai PPN")),
        "total_price": num(data.get("Grand Total")),
        "validity_text": txt(data.get("Masa Berlaku Penawaran")),
        "payment_mechanism": txt(data.get("Mekanisme Skema Pembayaran")),
        "currency": "IDR",
    }

    # line_no memakai urutan baca, bukan kolom "No" di dokumen: penomoran cetak kadang
    # berulang atau bergaya "1a/1b", dan itu melanggar UNIQUE (sph_id, line_no).
    items: list[dict[str, Any]] = []
    raw_items = data.get("Daftar Penawaran Harga") or []
    for i, row in enumerate(raw_items if isinstance(raw_items, list) else [], 1):
        if not isinstance(row, dict):
            continue
        desc = txt(row.get("Nama Barang/Jasa"))
        if not desc:
            continue
        items.append(
            {
                "_sph_ref": doc_key,
                "line_no": i,
                "category": txt(row.get("Kategori/Kelompok")),
                "description": desc,
                "specification": txt(row.get("Spesifikasi")),
                "brand": txt(row.get("Brand/Merek")),
                "part_number": txt(row.get("Part Number")),
                "quantity": num(row.get("Volume / Qty")),
                "unit": txt(row.get("Satuan")),
                "period": txt(row.get("Periode/Durasi")),
                "unit_price": num(row.get("Harga Satuan")),
                "line_total": num(row.get("Total Harga")),
                "charge_type": jenis_biaya(row.get("Jenis Biaya")),
                "remarks": txt(row.get("Keterangan")),
            }
        )

    return {
        "document": [document_row(result, doc_key, "sph")],
        "sph": [sph],
        "sph_item": items,
        "extracted_field": extracted_field_rows(result, doc_key),
        "extraction_run": [extraction_run_row(result, doc_key, SCHEMA_VERSION)],
    }
