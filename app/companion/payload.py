"""
Open ADE — Satu pintu dari hasil ekstraksi ke payload companion (skema dol_schema).

Dipakai oleh CLI (`scripts/companion.py`) dan API (`app/main.py`), supaya kedua jalur
menghasilkan tabel yang sama.

Hanya jenis dokumen yang tabelnya BERLAKU yang menghasilkan payload: kontrak dan SPH.
Tabel BAST masih usulan (status `ditunda` di dol_schema), jadi dokumen BAST tetap diekstrak
dan hasilnya tersimpan di `*.extract.json`, tetapi tidak dikirim ke NocoDB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.companion.contract_mapper import map_contract
from app.companion.sph_mapper import map_sph

SUPPORTED_TYPES = ("contract", "sph")
DEFERRED_TYPES = {
    "bast": "Tabel BAST masih usulan (ditunda di dol-schema); hasil ekstraksi BAST hanya "
    "tersimpan di *.extract.json sampai skemanya disepakati bersama RPA & Network.",
}


def payload_note(doc_type: str | None) -> str | None:
    """Alasan sebuah jenis dokumen tidak menghasilkan payload, untuk ditampilkan ke pemanggil."""
    t = (doc_type or "").lower()
    if t in SUPPORTED_TYPES:
        return None
    return DEFERRED_TYPES.get(t, f"Jenis dokumen '{t}' belum punya pemeta companion.")


def build_companion_payload(
    result: dict[str, Any], source_path: Path | None = None
) -> dict[str, list[dict[str, Any]]] | None:
    """
    Hasil ekstraksi -> {nama_tabel: [baris, ...]} sesuai dol_schema, atau None bila jenis
    dokumennya belum/tidak dikirim ke NocoDB (lihat `payload_note`).

    `source_path` sebaiknya berkas asli: kunci `document.content_hash` dihitung dari byte
    berkas itu, sama dengan `document_id` yang dibalas ke n8n.
    """
    doc_type = (result.get("document_type") or "").lower()
    src = source_path if source_path and Path(source_path).exists() else None
    if doc_type == "contract":
        return map_contract(result, pdf_path=src)
    if doc_type == "sph":
        return map_sph(result, pdf_path=src)
    return None
