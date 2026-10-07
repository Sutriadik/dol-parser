"""
Open ADE — Kirim payload companion ke NocoDB.

Model companion memakai foreign key berupa ID BARIS sungguhan, yang baru diketahui SETELAH
baris induknya masuk. Jadi modul ini:

1. mengirim tabel menurut `insert_order()` -- induk dulu, anak menyusul;
2. mencatat Id NocoDB tiap induk, lalu menukar `_document_ref` / `_contract_ref` pada baris
   anak menjadi `document_id` / `contract_id` yang nyata;
3. menerjemahkan nama teknis kolom ke JUDUL bahasa Indonesia (`dol_schema.to_nocodb_record`),
   karena API rekaman NocoDB memakai judul sebagai kunci JSON dan di klausa `where`.

Aturan yang dijaga di sini, bukan di pemanggil:

- **Hanya tabel yang berlaku.** Tabel `ditunda` (BAST, evidence) dan tabel khusus PostgreSQL
  (`extraction_run`) dilewati -- dan DILAPORKAN, tidak diam-diam.
- **Dokumen yang sudah mulai diperiksa PM tidak ditimpa.** Bila dokumen itu sudah punya baris
  di Keputusan PM (`field_review`), pengiriman ditolak kecuali `allow_reviewed=True` atas
  permintaan PM. Tanpa ini, proses ulang menggeser nilai dan nomor baris di bawah keputusan
  yang sudah dibuat.
- **Upsert per kunci alami, lalu bersihkan baris basi.** Baris anak milik induk yang sama yang
  tidak ada lagi di kiriman baru dihapus (mis. kontrak yang kini terbaca 3 item, bukan 5) --
  kecuali baris itu masih dirujuk tabel lain, maka pengiriman dihentikan.
- **Tabel tanpa kunci alami ditolak.** Strategi lama "hapus semua lalu isi ulang" menghapus
  kolom yang sudah diisi PM; skema kini mewajibkan kunci untuk setiap tabel yang dikirim.
- `field_review` milik PM: bukan cuma tidak dikirim, tapi ditolak kalau ada di payload.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import httpx

from app.logger import logger
from dol_schema import (
    ALL_TABLES,
    insert_order,
    natural_key,
    nocodb_title,
    table,
    to_nocodb_record,
    validate_payload,
)

__all__ = ["CompanionPusher", "CompanionPushError", "natural_key"]

_PAGE = 200


class CompanionPushError(RuntimeError):
    pass


class CompanionPusher:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        table_ids: dict[str, str],
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        # Token dicek saat push sungguhan, bukan di sini: rencana (--dry-run) harus bisa
        # dilihat sebelum NocoDB dipasang, supaya tabel yang perlu dibuat sudah diketahui.
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.table_ids = table_ids
        self.timeout = timeout
        self.transport = transport  # diisi tes dengan NocoDB tiruan
        self._ids: dict[tuple[str, str], int] = {}  # (tabel, ref) -> Id NocoDB
        self._pushed: dict[str, list[int]] = defaultdict(list)  # tabel -> Id yang dikirim

    # ------------------------------------------------------------------ HTTP
    def _headers(self) -> dict[str, str]:
        return {"xc-token": self.api_token, "Content-Type": "application/json"}

    def _url(self, table_name: str) -> str:
        tid = self.table_ids.get(table_name)
        if not tid:
            raise CompanionPushError(
                f"Tabel '{table_name}' belum ada di NOCODB_TABLE_IDS. "
                f"Jalankan: python scripts/nocodb_setup.py --list-tables"
            )
        return f"{self.base_url}/api/v2/tables/{tid}/records"

    @staticmethod
    def _esc(value: Any) -> str:
        """Nilai untuk klausa `where` NocoDB. Tanda kurung dan koma adalah sintaks di sana,
        jadi harus di-escape -- nilai seperti "List Item/Barang[0].Harga (Rp)" akan memotong
        query dan mencocokkan baris yang salah, bukan gagal dengan jelas."""
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace("(", "\\(")
            .replace(")", "\\)")
        )

    def _where(self, table_name: str, cond: dict[str, Any]) -> str:
        return "~and".join(
            f"({nocodb_title(table_name, k)},eq,{self._esc(v)})" for k, v in cond.items()
        )

    def _get_all(
        self, client: httpx.Client, table_name: str, cond: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Semua baris yang cocok, halaman demi halaman (NocoDB membatasi per halaman)."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            r = client.get(
                self._url(table_name),
                headers=self._headers(),
                params={"where": self._where(table_name, cond), "limit": _PAGE, "offset": offset},
            )
            r.raise_for_status()
            body = r.json()
            batch = body.get("list", [])
            rows += batch
            if len(batch) < _PAGE or (body.get("pageInfo") or {}).get("isLastPage", True):
                return rows
            offset += _PAGE

    @staticmethod
    def _ids_from_response(resp: httpx.Response) -> list[int]:
        body = resp.json()
        rows = body if isinstance(body, list) else [body]
        return [int(r["Id"]) for r in rows if isinstance(r, dict) and r.get("Id") is not None]

    # ------------------------------------------------------------------ ref FK
    def _resolve_refs(self, table_name: str, row: dict[str, Any]) -> dict[str, Any]:
        """`_contract_ref: "doc-..."` -> `contract_id: 41`. Baris yatim dihentikan, bukan
        dikirim dengan FK kosong -- baris tanpa induk tidak bisa ditemukan lagi oleh PM."""
        out = {}
        for key, value in row.items():
            if not key.startswith("_"):
                out[key] = value
                continue
            parent = key[1:-4]  # _contract_ref -> contract
            real = self._ids.get((parent, str(value)))
            if real is None:
                raise CompanionPushError(
                    f"{table_name}: induk '{parent}' untuk ref {value!r} tidak ditemukan. "
                    f"Baris induk gagal masuk lebih dulu."
                )
            out[f"{parent}_id"] = real
        return out

    # ------------------------------------------------------------------ penjaga
    def _guard_reviewed(
        self, client: httpx.Client, payload: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Tolak bila dokumen di payload sudah punya Keputusan PM."""
        if "field_review" not in self.table_ids:
            raise CompanionPushError(
                "field_review belum ada di NOCODB_TABLE_IDS: tidak bisa memastikan dokumen ini "
                "belum diperiksa PM, jadi tidak ada yang dikirim."
            )
        for doc in payload.get("document") or []:
            ada = self._get_all(client, "document", {"content_hash": doc["content_hash"]})
            if not ada:
                continue
            keputusan = self._get_all(client, "field_review", {"document_id": ada[0]["Id"]})
            if keputusan:
                raise CompanionPushError(
                    f"'{doc.get('source_filename')}' sudah punya {len(keputusan)} keputusan PM. "
                    f"Proses ulang tidak menimpa dokumen yang sedang/sudah diperiksa; kirim "
                    f"ulang hanya atas permintaan PM (allow_reviewed=True / --timpa-yang-direview)."
                )

    def _guard_not_referenced(
        self, client: httpx.Client, table_name: str, row_ids: list[int]
    ) -> None:
        """Baris yang akan dihapus tidak boleh masih dirujuk tabel lain."""
        for t in ALL_TABLES:
            if not t.in_nocodb or t.name not in self.table_ids:
                continue
            for c in t.columns:
                if c.fk != f"{table_name}.id":
                    continue
                for rid in row_ids:
                    if self._get_all(client, t.name, {c.name: rid}):
                        raise CompanionPushError(
                            f"Baris {table_name} Id={rid} tidak ada lagi di hasil baru, tapi "
                            f"masih dirujuk {t.name}.{c.name}. Tidak ada yang dihapus; cek "
                            f"dokumennya bersama PM."
                        )

    # ------------------------------------------------------------------ tulis
    def _upsert(
        self,
        client: httpx.Client,
        table_name: str,
        key: tuple[str, ...],
        rows: list[dict[str, Any]],
        ref_of: dict[int, str],
    ) -> dict[str, int]:
        inserted = updated = 0
        for i, row in enumerate(rows):
            found = self._get_all(client, table_name, {k: row[k] for k in key})
            record = to_nocodb_record(table_name, row)
            if found:
                new_id = int(found[0]["Id"])
                r = client.patch(
                    self._url(table_name), headers=self._headers(), json=[dict(record, Id=new_id)]
                )
                r.raise_for_status()
                updated += 1
            else:
                r = client.post(self._url(table_name), headers=self._headers(), json=[record])
                r.raise_for_status()
                got = self._ids_from_response(r)
                if not got:
                    raise CompanionPushError(
                        f"{table_name}: NocoDB tidak mengembalikan Id untuk baris baru; "
                        f"baris anak tidak bisa menunjuk ke sini."
                    )
                new_id, inserted = got[0], inserted + 1
            self._pushed[table_name].append(new_id)
            if i in ref_of:
                self._ids[(table_name, ref_of[i])] = new_id
        return {"inserted": inserted, "updated": updated}

    def _delete_stale(
        self,
        client: httpx.Client,
        table_name: str,
        key: tuple[str, ...],
        rows: list[dict[str, Any]],
    ) -> int:
        """Hapus baris anak milik induk yang sama yang tidak ada di kiriman baru."""
        t = table(table_name)
        fk_cols = [k for k in key if (t.column(k) and t.column(k).fk)]
        if not fk_cols:
            return 0  # bukan tabel anak (kunci tidak memuat induk)
        fk = fk_cols[0]
        parent = t.column(fk).fk.split(".")[0]
        keep = {tuple(r.get(k) for k in key) for r in rows}
        stale: list[int] = []
        for parent_id in set(self._pushed.get(parent, [])):
            for rec in self._get_all(client, table_name, {fk: parent_id}):
                rec_key = tuple(rec.get(nocodb_title(table_name, k)) for k in key)
                if rec_key not in keep:
                    stale.append(int(rec["Id"]))
        if not stale:
            return 0
        self._guard_not_referenced(client, table_name, stale)
        r = client.request(
            "DELETE",
            self._url(table_name),
            headers=self._headers(),
            json=[{"Id": x} for x in stale],
        )
        r.raise_for_status()
        return len(stale)

    # ------------------------------------------------------------------ entri
    def plan(self, payload: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        masalah = validate_payload(payload)
        if masalah:
            raise CompanionPushError(
                "Payload tidak lolos validasi, tidak dikirim. Perbaiki dulu:\n  "
                + "\n  ".join(masalah[:10])
            )
        semua = [n for n in insert_order() if n in payload]
        dilewati: dict[str, str] = {}
        urut: list[str] = []
        for n in semua:
            t = table(n)
            if t.written_by == "human":
                raise CompanionPushError(
                    f"'{n}' ditulis manusia; pipeline tidak boleh mengirim ke sini."
                )
            if t.status == "ditunda":
                dilewati[n] = "tabel usulan (ditunda), belum ada di NocoDB"
            elif not t.nocodb:
                dilewati[n] = "hanya PostgreSQL (metadata teknis internal)"
            elif not natural_key(t):
                raise CompanionPushError(
                    f"'{n}' tidak punya kunci anti-dobel; tidak bisa dikirim aman."
                )
            else:
                urut.append(n)
        return {
            "urutan": urut,
            "dilewati": dilewati,
            "strategi": {
                n: f"upsert per {'+'.join(natural_key(table(n)))}, lalu hapus baris basi"
                for n in urut
            },
            "baris": {n: len(payload[n]) for n in urut},
        }

    def push(
        self,
        payload: dict[str, list[dict[str, Any]]],
        dry_run: bool = False,
        allow_reviewed: bool = False,
    ) -> dict[str, Any]:
        rencana = self.plan(payload)
        if dry_run:
            return dict(rencana, dry_run=True)
        if not self.api_token:
            raise CompanionPushError(
                "NOCODB_API_TOKEN kosong. Ambil di NocoDB: foto profil > Account Settings "
                '> Tokens > Add New Token, lalu: export NOCODB_API_TOKEN="<token>"'
            )

        summary: dict[str, Any] = {}
        with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
            if not allow_reviewed:
                self._guard_reviewed(client, payload)
            for name in rencana["urutan"]:
                key = natural_key(table(name))
                # Nilai ref baris induk dicatat supaya anaknya bisa menunjuk ke Id nyata.
                if name == "document":
                    ref_of = {i: str(r["content_hash"]) for i, r in enumerate(payload[name])}
                else:
                    ref_of = {
                        i: str(r["_document_ref"])
                        for i, r in enumerate(payload[name])
                        if "_document_ref" in r
                    }
                rows = [self._resolve_refs(name, r) for r in payload[name]]
                try:
                    summary[name] = self._upsert(client, name, key, rows, ref_of)
                    summary[name]["deleted"] = self._delete_stale(client, name, key, rows)
                except httpx.HTTPError as e:
                    body = getattr(getattr(e, "response", None), "text", "")[:400]
                    raise CompanionPushError(f"Tabel '{name}' gagal: {e} {body}") from e

        hasil = {
            k: sum(v.get(k, 0) for v in summary.values())
            for k in ("inserted", "updated", "deleted")
        }
        logger.info(
            f"🗄  NocoDB companion: {hasil['inserted']} baru, {hasil['updated']} "
            f"diperbarui, {hasil['deleted']} baris basi dihapus"
        )
        if rencana["dilewati"]:
            logger.info(f"   dilewati sengaja: {', '.join(rencana['dilewati'])}")
        return dict(hasil, tables=summary, dilewati=rencana["dilewati"])
