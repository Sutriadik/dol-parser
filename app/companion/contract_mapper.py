"""
Open ADE — Pemeta hasil ekstraksi Kontrak / SPK / PKS -> baris tabel companion (dol_schema).

Masukan: isi `*.extract.json` (atau hasil `OpenADEEngine.process_full` jenis kontrak).
Keluaran: {nama_tabel: [baris, ...]} untuk tabel kontrak yang BERLAKU: document, contract,
contract_party, contract_item, contract_requirement, extracted_field, extraction_run.

Aturan yang dijaga di sini:
- **Tidak ada nilai karangan.** Nilai yang tidak terbaca dikirim kosong, bukan diisi pengganti
  ("(tidak terbaca)", nama berkas sebagai judul pekerjaan, kota bawaan). Kekosongan itu
  terlihat PM sebagai field `kosong` di Hasil Ekstraksi.
- **Peran pihak dari identitas, bukan dari sebutan.** "Pihak Pertama/Kedua" bisa menunjuk
  BUT atau pelanggan tergantung format dokumen. Pihak yang namanya cocok dengan BUT adalah
  `pelaksana`; pihak lainnya `pemberi_kerja`. Sebutan aslinya tetap disimpan.
- **Draf BAST tidak dibuat di sini.** Tabel BAST masih usulan; penyusunan BAST nanti membaca
  nilai yang sudah dikonfirmasi PM dari database (lihat app/companion/generator.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.companion.common import (
    document_key,
    document_row,
    evidence_for,
    extracted_field_rows,
    extraction_run_row,
    is_our_org,
    iso,
    num,
    parse_indonesian_date,
    txt,
)
from dol_schema import SCHEMA_VERSION

_SEBUTAN = {"Pihak Pertama": "PIHAK PERTAMA", "Pihak Kedua": "PIHAK KEDUA"}


def _roles(p1: dict[str, Any], p2: dict[str, Any]) -> dict[str, Any]:
    """-> {"Pihak Pertama": peran, "Pihak Kedua": peran, "catatan": teks|None}."""
    org1, org2 = txt(p1.get("Nama Perusahaan")), txt(p2.get("Nama Perusahaan"))
    kita1, kita2 = is_our_org(org1), is_our_org(org2)
    if kita1 and not kita2:
        return {"Pihak Pertama": "pelaksana", "Pihak Kedua": "pemberi_kerja", "catatan": None}
    if kita2 and not kita1:
        return {"Pihak Pertama": "pemberi_kerja", "Pihak Kedua": "pelaksana", "catatan": None}
    # Skema ekstraksi mendefinisikan Pihak Pertama = pemberi kerja (app/schemas/contract.py).
    # Dipakai hanya sebagai cadangan, dan PM diberi tahu bahwa perannya belum pasti.
    alasan = (
        "BUT tidak ditemukan di kedua pihak"
        if not (kita1 or kita2)
        else "kedua pihak tampak seperti BUT"
    )
    return {
        "Pihak Pertama": "pemberi_kerja",
        "Pihak Kedua": "pelaksana",
        "catatan": f"[warning] Peran pihak kontrak belum pasti ({alasan}); sementara "
        f"Pihak Pertama dianggap pemberi kerja. Mohon dicek.",
    }


def _jangka_waktu(teks: str | None):
    if not teks:
        return None, None
    tanggal = re.findall(
        r"\b\d{1,2}[-/ ]\d{1,2}[-/ ]\d{4}\b|\b\d{4}-\d{2}-\d{2}\b"
        r"|\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
        teks,
    )
    if len(tanggal) >= 2:
        return tanggal[0], tanggal[1]
    return None, None


def map_contract(
    result: dict[str, Any], pdf_path: Path | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Hasil ekstraksi kontrak -> {tabel: [baris]} sesuai dol_schema."""
    data = result.get("data") or {}
    doc_key = document_key(result, pdf_path)
    document = document_row(result, doc_key, "kontrak")

    tanggal_teks = txt(data.get("Tanggal Pembuatan Dokumen"))
    jangka = txt(data.get("Jangka Waktu"))
    mulai_teks, selesai_teks = _jangka_waktu(jangka)
    lampiran_mentah = data.get("Syarat Lampiran Wajib BAST") or []
    if not isinstance(lampiran_mentah, list):
        lampiran_mentah = [lampiran_mentah]
    lampiran = [txt(x) for x in lampiran_mentah if txt(x)]

    contract = {
        "_document_ref": doc_key,
        "contract_number": txt(data.get("Nomor Kontrak Kerja")),
        "contract_number_internal": txt(data.get("Nomor Kontrak Internal")),
        "work_title": txt(data.get("Nama Pekerjaan")),
        "location_text": txt(data.get("Lokasi")),
        "contract_date_text": tanggal_teks,
        "contract_date": iso(parse_indonesian_date(tanggal_teks)),
        "start_date_text": mulai_teks,
        "start_date": iso(parse_indonesian_date(mulai_teks)),
        "end_date_text": selesai_teks,
        "end_date": iso(parse_indonesian_date(selesai_teks)),
        "duration_text": txt(data.get("Durasi Kerja")) or jangka,
        "contract_value": num(data.get("Total Harga Pekerjaan")),
        "subtotal_value": num(data.get("sub total")),
        "vat_value": num(data.get("Total PPN")),
        "vat_percentage": txt(data.get("persentase ppn")),
        "currency": "IDR",
        "payment_mechanism": txt(data.get("Mekanisme Skema Pembayaran")),
        "penalty_terms": txt(data.get("Persentase Sanksi/Penalti")),
        "bast_terms": "\n".join(f"- {x}" for x in lampiran) or None,
        "bank_name": txt(data.get("Nama Bank")),
        "bank_account_number": txt(data.get("Nomor Rekening Bank")),
        "bank_account_name": txt(data.get("Nama Rekening Bank")),
    }

    p1, p2 = data.get("Pihak Pertama") or {}, data.get("Pihak Kedua") or {}
    peran = _roles(p1, p2)
    if peran["catatan"]:
        document["validation_notes"] = "\n".join(
            x for x in (document["validation_notes"], peran["catatan"]) if x
        )
    parties = []
    for sebutan, party in (("Pihak Pertama", p1), ("Pihak Kedua", p2)):
        org = txt(party.get("Nama Perusahaan"))
        if not org:
            continue  # pihak tanpa nama tidak bisa dikenali PM
        parties.append(
            {
                "_contract_ref": doc_key,
                "role": peran[sebutan],
                "party_label_text": _SEBUTAN[sebutan],
                "org_name_text": org,
                "signer_name": txt(party.get("Nama Representative")),
                "signer_title": txt(party.get("Jabatan")),
                "org_address_text": txt(party.get("Alamat")),
            }
        )

    # line_no = urutan baca. Kolom "No" di dokumen kadang berulang atau "1a/1b".
    items = []
    raw_items = data.get("List Item/Barang") or []
    for i, row in enumerate(raw_items if isinstance(raw_items, list) else [], 1):
        if not isinstance(row, dict):
            continue
        desc = txt(row.get("Deskripsi Item/Barang/Pekerjaan")) or txt(row.get("Deskripsi"))
        if not desc:
            continue
        items.append(
            {
                "_contract_ref": doc_key,
                "line_no": i,
                "category": txt(row.get("Kategori/Kelompok")),
                "description": desc,
                "specification": txt(row.get("Spesifikasi")),
                "quantity": num(row.get("volume")),
                "unit": txt(row.get("unit")),
                "period": txt(row.get("Periode/Durasi")),
                "unit_price": num(row.get("Harga Satuan")),
                "line_total": num(row.get("Jumlah Harga")),
                "remarks": txt(row.get("Keterangan")),
            }
        )

    # Lampiran wajib BAST = separuh checklist gabungan (briefing hlm. 11 & 15). Halaman dan
    # kutipan diambil dari bukti grounding, supaya PM bisa mengecek tanpa membaca ulang.
    # line_no = indeks mentah + 1, sama dengan "Syarat Lampiran Wajib BAST[i]" di Hasil
    # Ekstraksi -- generator mencari keputusan PM lewat indeks itu.
    requirements = []
    for i, mentah in enumerate(lampiran_mentah):
        teks = txt(mentah)
        if not teks:
            continue
        ev = evidence_for(result, f"Syarat Lampiran Wajib BAST[{i}]") or {}
        requirements.append(
            {
                "_contract_ref": doc_key,
                "line_no": i + 1,
                "requirement_type": "lampiran_wajib",
                "requirement_text": teks,
                "evidence_page": ev.get("page"),
                "evidence_quote": txt(ev.get("evidence_text")),
            }
        )

    return {
        "document": [document],
        "contract": [contract],
        "contract_party": parties,
        "contract_item": items,
        "contract_requirement": requirements,
        "extracted_field": extracted_field_rows(result, doc_key),
        "extraction_run": [extraction_run_row(result, doc_key, SCHEMA_VERSION)],
    }
