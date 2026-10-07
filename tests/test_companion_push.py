"""
Pengiriman payload companion ke NocoDB (app/companion/nocodb_push.py).

NocoDB tiruan di sini meniru sifat API rekaman v2 yang menentukan kebenaran pusher: kunci
JSON dan klausa `where` memakai JUDUL kolom (bahasa Indonesia), bukan nama teknis. Kunci yang
tidak dikenal ditolak, supaya pusher yang lupa menerjemahkan langsung gagal di tes.
"""

import json
import re

import httpx
import pytest

from app.companion.contract_mapper import map_contract
from app.companion.nocodb_push import CompanionPusher, CompanionPushError
from dol_schema import ALL_TABLES, table
from tests.test_companion_contract import _sample_contract_extraction

_TABLE_OF_ID = {f"tbl_{t.name}": t.name for t in ALL_TABLES}


def _nocodb_palsu():
    db: dict = {}
    berikut = {"n": 0}

    def judul_sah(nama_tabel: str):
        return {c.label for c in table(nama_tabel).columns} | {"Id"}

    def cocok(row: dict, where: str) -> bool:
        for bit in [b for b in where.split("~and") if b]:
            judul, _, nilai = bit.strip("()").partition(",eq,")
            nilai = re.sub(r"\\(.)", r"\1", nilai)
            if str(row.get(judul)) != nilai:
                return False
        return True

    def handler(request: httpx.Request) -> httpx.Response:
        tid = request.url.path.split("/")[-2]
        nama = _TABLE_OF_ID[tid]
        rows = db.setdefault(nama, [])
        if request.method == "GET":
            where = request.url.params.get("where", "")
            for bit in [b for b in where.split("~and") if b]:
                assert bit.strip("(").split(",eq,")[0] in judul_sah(nama), f"where: {bit}"
            hit = [r for r in rows if cocok(r, where)]
            off = int(request.url.params.get("offset", 0))
            lim = int(request.url.params.get("limit", 25))
            return httpx.Response(
                200,
                json={
                    "list": hit[off : off + lim],
                    "pageInfo": {"isLastPage": off + lim >= len(hit)},
                },
            )
        body = json.loads(request.content)
        for b in body:
            asing = set(b) - judul_sah(nama)
            assert not asing, f"{nama}: kunci bukan judul NocoDB: {asing}"
        if request.method == "POST":
            keluar = []
            for b in body:
                berikut["n"] += 1
                rows.append(dict(b, Id=berikut["n"]))
                keluar.append({"Id": berikut["n"]})
            return httpx.Response(200, json=keluar)
        if request.method == "PATCH":
            for b in body:
                next(r for r in rows if r["Id"] == b["Id"]).update(b)
            return httpx.Response(200, json=body)
        if request.method == "DELETE":
            buang = {b["Id"] for b in body}
            db[nama] = [r for r in rows if r["Id"] not in buang]
            return httpx.Response(200, json=body)
        return httpx.Response(405)

    return db, httpx.MockTransport(handler)


def _pusher(transport, tanpa=()):
    ids = {t.name: f"tbl_{t.name}" for t in ALL_TABLES if t.name not in tanpa}
    return CompanionPusher("http://uji", "token-uji", ids, transport=transport)


def _payload(**ubah):
    raw = _sample_contract_extraction()
    raw["data"].update(ubah)
    return map_contract(raw)


# ---------------------------------------------------------------- bentuk kiriman
def test_kiriman_memakai_judul_indonesia_dan_fk_nyata():
    db, transport = _nocodb_palsu()
    hasil = _pusher(transport).push(_payload())

    kontrak = db["contract"][0]
    assert kontrak["Nomor Kontrak"] == "123/00/XYZ-01/BUT/2026"
    assert kontrak["ID Dokumen"] == db["document"][0]["Id"]
    assert all(r["ID Kontrak"] == kontrak["Id"] for r in db["contract_item"])
    assert {r["Peran"] for r in db["contract_party"]} == {"pemberi_kerja", "pelaksana"}
    assert hasil["inserted"] > 0 and hasil["deleted"] == 0


def test_tabel_audit_dan_usulan_dilewati_dan_dilaporkan():
    db, transport = _nocodb_palsu()
    hasil = _pusher(transport).push(_payload())
    assert "extraction_run" not in db
    assert "extraction_run" in hasil["dilewati"]


def test_rencana_dry_run_tanpa_token():
    rencana = CompanionPusher("http://uji", "", {}).push(_payload(), dry_run=True)
    assert rencana["urutan"][0] == "document" and "field_review" not in rencana["urutan"]
    assert "hapus baris basi" in rencana["strategi"]["contract_item"]


# ---------------------------------------------------------------- proses ulang
def test_proses_ulang_tidak_menggandakan_baris():
    db, transport = _nocodb_palsu()
    _pusher(transport).push(_payload())
    _pusher(transport).push(_payload())
    assert len(db["document"]) == 1 and len(db["contract"]) == 1
    assert len(db["contract_party"]) == 2 and len(db["contract_item"]) == 3


def test_baris_yang_hilang_di_hasil_baru_ikut_dihapus():
    """Kontrak 3 item yang kini terbaca 1 item tidak boleh tetap menampilkan 3 baris."""
    db, transport = _nocodb_palsu()
    _pusher(transport).push(_payload())
    payload = _payload()
    payload["contract_item"] = payload["contract_item"][:1]
    payload["extracted_field"] = payload["extracted_field"][:1]
    hasil = _pusher(transport).push(payload)
    assert len(db["contract_item"]) == 1
    assert len(db["extracted_field"]) == 1
    assert hasil["deleted"] == 2 + (len(_payload()["extracted_field"]) - 1)


def test_daftar_anak_kosong_tetap_membersihkan_baris_lama():
    db, transport = _nocodb_palsu()
    _pusher(transport).push(_payload())
    payload = _payload()
    payload["contract_requirement"] = []
    _pusher(transport).push(payload)
    assert db.get("contract_requirement") == []


# ---------------------------------------------------------------- keputusan PM
def test_dokumen_yang_sudah_direview_pm_tidak_ditimpa():
    db, transport = _nocodb_palsu()
    _pusher(transport).push(_payload())
    db.setdefault("field_review", []).append(
        {
            "Id": 999,
            "ID Dokumen": db["document"][0]["Id"],
            "Nama Field": "Nomor Kontrak Kerja",
            "Keputusan": "benar",
        }
    )
    with pytest.raises(CompanionPushError, match="keputusan PM"):
        _pusher(transport).push(_payload(**{"Nama Pekerjaan": "Judul hasil OCR baru"}))
    assert db["contract"][0]["Nama Pekerjaan"].startswith("Pengadaan Perangkat Switch")


def test_timpa_yang_direview_hanya_bila_diminta():
    db, transport = _nocodb_palsu()
    _pusher(transport).push(_payload())
    db.setdefault("field_review", []).append({"Id": 999, "ID Dokumen": db["document"][0]["Id"]})
    _pusher(transport).push(_payload(**{"Nama Pekerjaan": "Judul baru"}), allow_reviewed=True)
    assert db["contract"][0]["Nama Pekerjaan"] == "Judul baru"
    assert len(db["field_review"]) == 1  # keputusan PM tidak pernah disentuh


def test_tanpa_tabel_keputusan_pm_tidak_ada_yang_dikirim():
    db, transport = _nocodb_palsu()
    with pytest.raises(CompanionPushError, match="field_review"):
        _pusher(transport, tanpa=("field_review",)).push(_payload())
    assert db == {}


def test_payload_berisi_tabel_milik_pm_ditolak():
    payload = _payload()
    payload["field_review"] = [
        {
            "document_id": 1,
            "field_path": "x",
            "decision": "benar",
            "reviewed_by": "bot",
            "reviewed_at": "2026-01-01T00:00:00Z",
        }
    ]
    with pytest.raises(CompanionPushError):
        CompanionPusher("http://uji", "", {}).push(payload, dry_run=True)
