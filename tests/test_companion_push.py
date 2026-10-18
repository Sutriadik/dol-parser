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


# bentuk kiriman
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


# proses ulang
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


# keputusan PM
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


# Tampilan siap pakai untuk PM (scripts/nocodb_setup.py --create-views)
#
# Tampilan dibuat lewat API meta NocoDB dari TAMPILAN_PM, KOLOM_TAMPILAN, dan FORM_KEPUTUSAN.
# Daftar itu merujuk nama teknis dol-schema; bila sebuah kolom atau pilihan nilai diganti di
# skema, tampilan akan gagal dibuat di NocoDB sungguhan -- tes ini membuatnya gagal lebih dulu.


def _judul_tampilan(tabel_nama: str) -> set[str]:
    from scripts import nocodb_setup as ns

    return {k["judul"] for k in ns.KOLOM_TAMPILAN if k["tabel"] == tabel_nama}


def test_tampilan_pm_hanya_merujuk_tabel_kolom_dan_pilihan_yang_ada():
    from dol_schema import to_nocodb_fields
    from scripts import nocodb_setup as ns

    spec = to_nocodb_fields()["tables"]
    judul_terpakai = set()
    for t in ns.TAMPILAN_PM:
        assert t["tabel"] in spec, f"{t['judul']}: tabel {t['tabel']} bukan tabel berlaku"
        kolom = {c.name: c for c in table(t["tabel"]).columns}
        boleh = set(kolom) | _judul_tampilan(t["tabel"])
        dirujuk = (
            list(t["kolom"])
            + list(t.get("kelompok", []))
            + [k for k, _, _ in t.get("filter", [])]
            + [k for k, _ in t.get("urut", []) if k != "_dibuat"]
        )
        assert not set(dirujuk) - boleh, f"{t['judul']}: {set(dirujuk) - boleh}"
        for k, op, nilai in t.get("filter", []):
            assert op in {"anyof", "like", "eq", "neq"}
            if k in kolom and kolom[k].enum:
                assert set(nilai.split(",")) <= set(kolom[k].enum), f"{t['judul']}: {nilai}"
        assert all(arah in ("asc", "desc") for _, arah in t.get("urut", []))
        assert (t["tabel"], t["judul"]) not in judul_terpakai, "judul tampilan kembar"
        judul_terpakai.add((t["tabel"], t["judul"]))
    f = ns.FORM_KEPUTUSAN
    assert set(f["kolom"]) <= {c.name for c in table(f["tabel"]).columns}
    assert set(f["wajib"]) <= set(f["kolom"])


def test_rumus_hanya_memakai_fungsi_yang_ada_di_nocodb_sqlite():
    """Diuji di NocoDB 2026.08 dengan SQLite: fungsi ini membuat kolom gagal dibuat (500)."""
    from scripts import nocodb_setup as ns

    for k in ns.KOLOM_TAMPILAN:
        rumus = k.get("rumus") or k.get("tombol") or ""
        assert not re.search(r"\b(FIND|SUBSTITUTE|VALUE|INT|REGEX_\w+)\(", rumus), k["judul"]
        # Perbandingan memakai ==; "{x} = y" ditolak parser rumus NocoDB.
        assert not re.search(r"[^=!<>]=[^=]", re.sub(r'"[^"]*"', '""', rumus)), k["judul"]


def test_tombol_tidak_meng_encode_teks_gabungan():
    """URLENCODE atas CONCAT panjang membuat SQLite gagal membaca seluruh tabel
    ("parser stack overflow"): grid Rincian SPH tidak bisa dibuka sama sekali."""
    from scripts import nocodb_setup as ns

    tombol = [k for k in ns.KOLOM_TAMPILAN if "tombol" in k]
    assert {k["tabel"] for k in tombol} == {"extracted_field", "sph_item", "contract_item"}
    for k in tombol:
        assert "URLENCODE(CONCAT(" not in k["tombol"], k["judul"]
        assert "{FORM}?" in k["tombol"]


def test_bagian_harga_dan_rincian_konsisten_dengan_rumusnya():
    """Harga Wajib Dicek dulu disaring `Nama Field like Harga` dan ikut menangkap semua field
    SPH ("Daftar Penawaran Harga[0].Brand/Merek"). Sekarang disaring per Bagian."""
    from scripts import nocodb_setup as ns

    assert ns._BAGIAN_NILAI in ns._RUMUS_BAGIAN and ns._BAGIAN_RINCIAN in ns._RUMUS_BAGIAN
    harga = next(t for t in ns.TAMPILAN_PM if t["judul"] == "Harga Wajib Dicek")
    assert harga["filter"] == [("Bagian", "eq", ns._BAGIAN_NILAI)]


def _nocodb_meta_palsu():
    """Tiruan API meta NocoDB sebatas yang dipakai --create-views; mencatat semua tulisan."""
    from dol_schema import to_nocodb_fields

    spec = to_nocodb_fields()["tables"]
    catatan = {
        "views": {},
        "filters": {},
        "sorts": {},
        "kolom_view": {},
        "kelompok": {},
        "kolom_baru": {},
        "share": [],
        "form_kolom": {},
        "kunci": set(),
    }
    kolom_tabel = {}
    for nama, s in spec.items():
        fk = {c.name: c.fk.split(".")[0] for c in table(nama).columns if c.fk}
        kolom = [
            {"id": f"{nama}.Id", "title": "Id", "uidt": "ID"},
            {"id": f"{nama}.CreatedAt", "title": "CreatedAt", "uidt": "CreatedTime"},
        ]
        for f in s["columns"]:
            uidt = "ForeignKey" if f["column_name"] in fk else f["uidt"]
            c = {"id": f"{nama}.{f['column_name']}", "title": f["title"], "uidt": uidt}
            c["column_name"] = f["column_name"]
            if f.get("options"):
                c["colOptions"] = {"options": [{"title": o} for o in f["options"]]}
            kolom.append(c)
        kolom_tabel[nama] = kolom
    for nama in spec:
        for induk in {c.fk.split(".")[0] for c in table(nama).columns if c.fk}:
            kolom_tabel[nama].append(
                {
                    "id": f"{nama}.link.{induk}",
                    "title": spec[induk]["title"],
                    "uidt": "LinkToAnotherRecord",
                }
            )
            kolom_tabel[induk].append(
                {"id": f"{induk}.links.{nama}", "title": spec[nama]["title"], "uidt": "Links"}
            )
    view_kolom = {}

    def handler(request: httpx.Request) -> httpx.Response:
        bagian = request.url.path.split("/")[4:]  # setelah /api/v2/meta
        body = json.loads(request.content) if request.content else None
        m = request.method
        if bagian[0] == "bases":
            return httpx.Response(
                200, json={"list": [{"title": s["title"], "id": n} for n, s in spec.items()]}
            )
        if bagian[0] == "tables" and len(bagian) == 2:
            return httpx.Response(200, json={"columns": kolom_tabel[bagian[1]]})
        if bagian[0] == "tables" and bagian[2] == "columns":
            cid = f"{bagian[1]}.v.{body['title']}"
            kolom_tabel[bagian[1]].append({"id": cid, "title": body["title"], "uidt": body["uidt"]})
            catatan["kolom_baru"][cid] = body
            return httpx.Response(200, json={})
        if bagian[0] == "columns":
            return httpx.Response(200, json={})
        if bagian[0] == "tables" and bagian[2] == "views":
            ada = [v for v in catatan["views"].values() if v["tabel"] == bagian[1]]
            return httpx.Response(200, json={"list": ada})
        if bagian[0] == "tables" and bagian[2] in ("grids", "forms"):
            vid = f"vw{len(catatan['views']) + 1}"
            catatan["views"][vid] = {"id": vid, "title": body["title"], "tabel": bagian[1]}
            view_kolom[vid] = [
                {"id": f"vc:{vid}:{c['id']}", "fk_column_id": c["id"], "show": 1, "order": i}
                for i, c in enumerate(kolom_tabel[bagian[1]], start=1)
            ]
            return httpx.Response(200, json={"id": vid})
        if bagian[0] == "forms":
            return httpx.Response(200, json={})
        if bagian[0] in ("form-columns", "grid-columns"):
            kunci = "form_kolom" if bagian[0] == "form-columns" else "kelompok"
            vid, cid = bagian[1].split(":")[1], bagian[1].split(":", 2)[2]
            catatan[kunci].setdefault(vid, {})[cid] = body
            return httpx.Response(200, json={})
        vid = bagian[1]
        if len(bagian) == 2 and m == "PATCH":
            catatan["kunci"].add(vid) if body.get("lock_type") == "locked" else None
            return httpx.Response(200, json={})
        if bagian[2] == "share":
            catatan["share"].append((m, body))
            return httpx.Response(200, json={"uuid": "u-1"})
        if bagian[2] in ("filters", "sorts"):
            catatan[bagian[2]].setdefault(vid, []).append(body)
            return httpx.Response(200, json=body)
        if bagian[2] == "columns" and m == "GET":
            return httpx.Response(200, json={"list": view_kolom[vid]})
        if bagian[2] == "columns" and m == "PATCH":
            cid = bagian[3].split(":", 2)[2]
            catatan["kolom_view"].setdefault(vid, {})[cid] = body
            return httpx.Response(200, json=1)
        return httpx.Response(405)

    return catatan, httpx.MockTransport(handler), spec


def _tampilan(catatan, judul):
    return next(v for v in catatan["views"].values() if v["title"] == judul)


def _siapkan():
    from scripts import nocodb_setup as ns

    catatan, transport, spec = _nocodb_meta_palsu()
    with httpx.Client(transport=transport) as client:
        ns.buat_tampilan(client, "http://uji", "base-uji", spec)
    return catatan


def test_tampilan_pm_dibuat_sekali_dan_tidak_ditimpa_saat_diulang():
    """PM boleh mengubah filter tampilannya sendiri; menjalankan ulang tidak boleh menghapusnya."""
    from scripts import nocodb_setup as ns

    catatan, transport, spec = _nocodb_meta_palsu()
    with httpx.Client(transport=transport) as client:
        assert ns.buat_tampilan(client, "http://uji", "base-uji", spec) == len(ns.TAMPILAN_PM)
        assert ns.buat_tampilan(client, "http://uji", "base-uji", spec) == 0
    assert len(catatan["views"]) == len(ns.TAMPILAN_PM) + 1  # + form Keputusan PM
    assert len(catatan["kolom_baru"]) == len(ns.KOLOM_TAMPILAN)


def test_antrean_pm_berisiko_belum_diputuskan_dan_tanpa_rincian():
    from scripts import nocodb_setup as ns

    catatan = _siapkan()
    vid = _tampilan(catatan, "Antrean PM")["id"]
    status, belum, bukan_rincian = catatan["filters"][vid]
    assert status["fk_column_id"] == "extracted_field.system_status"
    assert status["comparison_op"] == "anyof"
    assert "bukti_kuat" not in status["value"].split(",")
    assert (belum["fk_column_id"], belum["comparison_op"], belum["value"]) == (
        "extracted_field.v.Jumlah Keputusan",
        "eq",
        "0",
    )
    assert bukan_rincian["value"] == ns._BAGIAN_RINCIAN
    assert catatan["sorts"][vid] == [
        {"fk_column_id": "extracted_field.v.Urutan Risiko", "direction": "asc"},
        {"fk_column_id": "extracted_field.confidence", "direction": "asc"},
    ]
    kelompok = catatan["kelompok"][vid]
    assert kelompok["extracted_field.link.document"]["group_by_order"] == 1
    assert kelompok["extracted_field.v.Bagian"]["group_by_order"] == 2
    assert vid in catatan["kunci"]


def test_kolom_panjang_disembunyikan_dan_fk_tampil_sebagai_relasi():
    catatan = _siapkan()
    dokumen = catatan["kolom_view"][_tampilan(catatan, "Dokumen Masuk")["id"]]
    assert dokumen["document.markdown"] == {"show": False}  # markdown utuh merusak grid
    assert dokumen["document.source_filename"]["show"] is True
    assert "document.Id" not in dokumen  # kolom sistem tidak disentuh

    antrean = catatan["kolom_view"][_tampilan(catatan, "Antrean PM")["id"]]
    # PM melihat relasi "Dokumen" yang bisa diklik, bukan angka "ID Dokumen".
    assert antrean["extracted_field.link.document"] == {"show": True, "order": 1}
    assert antrean["extracted_field.document_id"] == {"show": False}


def test_form_keputusan_dibagikan_dengan_isian_dari_url():
    """Tanpa preFillEnabled, parameter URL dari tombol Putuskan diabaikan dan form kosong."""
    catatan = _siapkan()
    patch = [b for m, b in catatan["share"] if m == "PATCH"]
    assert patch and patch[-1]["meta"]["preFillEnabled"] is True
    form = catatan["form_kolom"][_tampilan(catatan, "Catat Keputusan")["id"]]
    assert form["field_review.decision"]["required"] is True
    assert form["field_review.reviewed_by"]["required"] is True
    tombol = catatan["kolom_baru"]["extracted_field.v.Putuskan"]
    assert tombol["formula_raw"].count("http://uji/nc/form/u-1?") == 1
    assert "{Nama Field}" in tombol["formula_raw"]  # nama teknis sudah diganti judul skema
    assert "&Hasil+Ekstraksi=" in tombol["formula_raw"]
