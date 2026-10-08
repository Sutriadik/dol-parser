"""
Open ADE — Pemeta hasil ekstraksi BAST -> baris tabel companion (USULAN).

Tabel BAST di dol_schema berstatus **ditunda**: belum disepakati dengan RPA & Network
Engineer, jadi hasil pemeta ini TIDAK dikirim ke NocoDB (`payload.py` menolaknya). Modul ini
tetap dipelihara karena memuat temuan dari 14 BAST asli yang dipakai di workshop skema:

1. **Menurunkan peran & arah BAST.** Label "PIHAK PERTAMA/KEDUA" tidak menentukan peran:
   pada 14 BAST asli, pihak pertama adalah penyerah di format Telkom tapi penerima di
   format kampus. Peran dibaca dari kalimat "menyerahkan"; arah lalu diturunkan dari peran
   BUT -- menyerahkan -> BAST Pelanggan, menerima -> BAST Vendor (briefing hlm. 10).
2. **Memisahkan nilai mentah dan terparse.** Teks asli selalu disimpan; hasil parse boleh
   NULL. Baris tidak pernah gagal masuk hanya karena tanggalnya aneh.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.companion.common import (
    document_key,
    document_row,
    extracted_field_rows,
    extraction_run_row,
    is_our_org,
    iso,
    num,
    parse_angka_kata,
    parse_indonesian_date,
    txt,
)
from dol_schema import SCHEMA_VERSION

__all__ = [
    "map_bast",
    "derive_roles_and_direction",
    "detect_handover_party",
    "parse_indonesian_date",
    "parse_angka_kata",
]


def detect_handover_party(markdown: str) -> str | None:
    """
    -> "first" | "second" | None : pihak mana yang MENYERAHKAN, dibaca dari kalimatnya.

    Diuji pada 14 BAST: pola terbaca di 11 dokumen, 3 sisanya (format instansi & vendor)
    tidak memakai kalimat baku ini.
    """
    t = re.sub(r"\s+", " ", markdown[:6000])

    # Format kampus: "PIHAK KEDUA menyerahkan kepada PIHAK PERTAMA ..."
    m = re.search(r"pihak\s+(pertama|kedua)\s+menyerahkan\s+kepada", t, re.I)
    if m:
        return "first" if m.group(1).lower() == "pertama" else "second"

    # Format Telkom: "... Selanjutnya PIHAK PERTAMA" lalu heading "MENYERAHKAN"
    if re.search(r"##\s*MENYERAHKAN", t, re.I):
        m = re.search(r"selanjutnya\s+pihak\s+(pertama|kedua)", t, re.I)
        if m:
            return "first" if m.group(1).lower() == "pertama" else "second"
    return None


def derive_roles_and_direction(
    first_org: str | None, second_org: str | None, markdown: str
) -> tuple[str, str, str]:
    """
    -> (peran_pihak_pertama, peran_pihak_kedua, direction)

    Dua langkah terpisah, dan urutannya penting:

    1. **Peran** dibaca dari kalimat "menyerahkan" di dokumen. Label "Pihak Pertama/Kedua"
       TIDAK menentukan peran -- pada 14 BAST, pihak pertama adalah penyerah di format
       Telkom tapi penerima di format kampus.
    2. **Arah** diturunkan dari peran BUT, bukan dari posisinya. BUT menyerahkan ->
       `customer` (kita -> pelanggan); BUT menerima -> `vendor` (briefing hlm. 10).

    Versi pertama fungsi ini menurunkan arah langsung dari posisi BUT, dan itu salah:
    pada BAST lisensi perangkat lunak, BUT ada di Pihak Kedua tetapi menyerahkan, sehingga arahnya
    terbaca `vendor` padahal seharusnya `customer`.
    """
    handover_side = detect_handover_party(markdown)
    if handover_side == "first":
        first_role, second_role = "handover", "receiver"
    elif handover_side == "second":
        first_role, second_role = "receiver", "handover"
    else:
        # Kalimatnya tidak terbaca: jangan menebak peran dari posisi.
        return "handover", "receiver", "unknown"

    our_role = (
        first_role if is_our_org(first_org) else second_role if is_our_org(second_org) else None
    )
    direction = {"handover": "customer", "receiver": "vendor"}.get(our_role, "unknown")
    return first_role, second_role, direction


def _basis_doc_type(number: str | None, markdown: str) -> str | None:
    hay = f"{number or ''} {markdown[:4000]}".lower()
    for pattern, value in (
        (r"nota\s+pesanan", "order_note"),
        (r"surat\s+pesanan|inaproc", "order_note"),
        (r"\bpks\b|perjanjian\s+kerja", "pks"),
        (r"surat\s+perintah\s+kerja|\bspk\b", "spk"),
        (r"purchase\s+order|\bpo\b\s*/", "purchase_order"),
        (r"kontrak", "contract"),
    ):
        if re.search(pattern, hay):
            return value
    return None


def _vat_mode(markdown: str) -> str:
    head = markdown[:4000].lower()
    if re.search(r"belum\s+termasuk\s+ppn", head):
        return "excluded"
    if re.search(r"(sudah|termasuk)\s+(termasuk\s+)?ppn|sesudah\s+ppn", head):
        return "included"
    return "unstated"


def map_bast(result: dict[str, Any], pdf_path=None) -> dict[str, list[dict[str, Any]]]:
    """Hasil ekstraksi BAST -> {tabel: [baris]} sesuai tabel usulan dol_schema."""
    data = result.get("data") or {}
    markdown = result.get("markdown") or ""
    doc_key = document_key(result, pdf_path)

    first = data.get("Pihak Pertama") or {}
    second = data.get("Pihak Kedua") or {}
    first_org, second_org = txt(first.get("Nama Perusahaan")), txt(second.get("Nama Perusahaan"))
    first_role, second_role, direction = derive_roles_and_direction(first_org, second_org, markdown)

    handover_text = txt(data.get("Tanggal Serah Terima"))
    basis_number = txt(data.get("Nomor PO / Kontrak"))
    basis_date_text = txt(data.get("Tanggal PO / Kontrak"))

    bast = {
        "_document_ref": doc_key,
        "direction": direction,
        "contract_id": None,
        "bast_number_customer": txt(data.get("Nomor BAST")),
        "bast_number_internal": None,  # format Telkom punya nomor kedua; belum diekstrak
        "handover_date_text": handover_text,
        "handover_date": iso(parse_indonesian_date(handover_text)),
        "handover_city": None,
        "work_title": txt(data.get("Nama Pekerjaan")),
        "basis_doc_type": _basis_doc_type(basis_number, markdown),
        "basis_doc_number": basis_number,
        "basis_doc_date_text": basis_date_text,
        "basis_doc_date": iso(parse_indonesian_date(basis_date_text)),
        "basis_doc_value": num(data.get("Nilai Pengadaan")),
        "basis_doc_value_vat": _vat_mode(markdown),
        "currency": "IDR",
        "acceptance_statement": txt(data.get("Pernyataan Penerimaan"))
        or txt(data.get("Hasil Uji Terima Keseluruhan")),
    }

    parties = []
    for party, role in ((first, first_role), (second, second_role)):
        org = txt(party.get("Nama Perusahaan"))
        if not org:
            continue
        parties.append(
            {
                "_bast_ref": doc_key,
                "role": role,
                "org_name_text": org,
                "signer_name": txt(party.get("Nama Representative")),
                "signer_title": txt(party.get("Jabatan")),
                "org_address_text": txt(party.get("Alamat")),
            }
        )

    items = []
    raw_items = data.get("Daftar Barang/Pekerjaan Diserahkan")
    for i, row in enumerate(raw_items if isinstance(raw_items, list) else [], 1):
        if not isinstance(row, dict):
            continue
        desc = txt(row.get("Deskripsi"))
        if not desc:
            continue
        items.append(
            {
                "_bast_ref": doc_key,
                "line_no": i,
                "description": desc,
                "quantity": num(row.get("Volume")),
                "unit": txt(row.get("Satuan")),
                "unit_price": None,
                "line_total": None,
                "test_result": txt(row.get("Hasil Uji Terima")),
                "remarks": txt(row.get("Keterangan")),
            }
        )

    conditions = []

    def add_condition(
        ctype: str, text: str | None = None, number: float | None = None, dt: date | None = None
    ) -> None:
        if text is None and number is None and dt is None:
            return
        conditions.append(
            {
                "_bast_ref": doc_key,
                "condition_type": ctype,
                "value_text": text,
                "value_number": number,
                "value_date": iso(dt),
            }
        )

    aktif = txt(data.get("Tanggal Aktivasi Layanan"))
    add_condition("service_active_since", aktif, dt=parse_indonesian_date(aktif))
    uji = txt(data.get("Tanggal Uji Terima"))
    add_condition("acceptance_test_ref", uji, dt=parse_indonesian_date(uji))
    for doc in data.get("Dokumen Pendukung") or []:
        if txt(doc):
            add_condition("supporting_document", txt(doc))
    m = re.search(r"progress[^\n]{0,40}?(\d{1,3})\s*%", markdown, re.I)
    if m:
        add_condition("progress_percent", m.group(0).strip(), number=float(m.group(1)))
    m = re.search(r"\(\s*([A-Za-z][A-Za-z\s]{10,200}?rupiah)\s*\)", markdown, re.I)
    if m:
        add_condition("amount_in_words", re.sub(r"\s+", " ", m.group(1)).strip())

    return {
        "document": [document_row(result, doc_key, "bast")],
        "bast": [bast],
        "bast_party": parties,
        "bast_item": items,
        "bast_condition": conditions,
        "extracted_field": extracted_field_rows(result, doc_key),
        "extraction_run": [extraction_run_row(result, doc_key, SCHEMA_VERSION)],
        # field_review sengaja TIDAK dihasilkan: tabel itu milik manusia.
    }
