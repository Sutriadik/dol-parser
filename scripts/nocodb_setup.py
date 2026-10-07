#!/usr/bin/env python3
"""
Open ADE — penyiapan & uji sambungan NocoDB untuk skema dol-schema.

    python scripts/nocodb_setup.py --create-base ["DOL Schema 2026.10.3"]
        Buat base berisi tabel dol-schema yang BERLAKU (judul Indonesia) dengan kolom FK
        sebagai relasi Link, lalu cetak NOCODB_TABLE_IDS-nya. Aman diulang; base lain
        tidak disentuh.

    python scripts/nocodb_setup.py --list-tables
        Tampilkan base & tabel beserta tableId, siap disalin ke NOCODB_TABLE_IDS.

    python scripts/nocodb_setup.py --check
        Periksa satu per satu: URL terjangkau, token valid, setiap tableId ada.

    python scripts/nocodb_setup.py --compare-schema
        Cocokkan tabel di NocoDB dengan dol-schema, kolom per kolom (judul, tipe, pilihan).

Mengirim payload: python scripts/companion.py --push FILE.companion.json
Env yang dibaca: NOCODB_URL, NOCODB_API_TOKEN, NOCODB_TABLE_IDS
(app/config.py tidak memuat .env sendiri: jalankan `set -a; . ./.env; set +a` dulu).
"""

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import config  # noqa: E402
from dol_schema import (  # noqa: E402
    SCHEMA_VERSION,
    natural_key,
    table,
    table_name_for_title,
    to_nocodb_fields,
)

# Kolom yang dibuat NocoDB sendiri di setiap tabel; bukan bagian model.
_NOCODB_SYSTEM_UIDT = {
    "ID",
    "CreatedTime",
    "LastModifiedTime",
    "CreatedBy",
    "LastModifiedBy",
    "Order",
    "Deleted",
}
_LINK_UIDT = {"LinkToAnotherRecord", "Links"}


def _relasi(name: str) -> dict[str, str]:
    """Kolom FK tabel ini -> tabel induknya, dari `Column.fk` di dol-schema."""
    return {c.name: c.fk.split(".")[0] for c in table(name).columns if c.fk}


def _headers() -> dict:
    return {"xc-token": config.NOCODB_API_TOKEN, "Content-Type": "application/json"}


def _require_token() -> bool:
    if not config.NOCODB_API_TOKEN:
        print("❌ NOCODB_API_TOKEN kosong.")
        print("   Ambil di NocoDB: foto profil > Account Settings > Tokens > Add New Token")
        print('   Lalu: export NOCODB_API_TOKEN="<token>"')
        return False
    return True


def _column_spec(field: dict) -> dict:
    """Spesifikasi kolom dol-schema -> body kolom API meta NocoDB."""
    col = {"column_name": field["column_name"], "title": field["title"], "uidt": field["uidt"]}
    if field.get("options"):
        col["colOptions"] = {"options": [{"title": o} for o in field["options"]]}
    if field["uidt"] == "Currency":
        # Tanpa ini NocoDB memakai bawaan USD dan PM melihat "$" pada nilai Rupiah.
        col["meta"] = {"currency_locale": "id-ID", "currency_code": "IDR"}
    if field.get("description"):
        col["description"] = field["description"]  # tooltip kolom di UI NocoDB
    if field.get("pv"):
        col["pv"] = True  # judul baris: nomor kontrak, bukan Id
    return col


def cmd_list_tables() -> int:
    if not _require_token():
        return 1
    base_url = config.NOCODB_URL.rstrip("/")
    try:
        with httpx.Client(timeout=15.0) as client:
            bases = client.get(f"{base_url}/api/v2/meta/bases", headers=_headers())
            bases.raise_for_status()
            pairs = []
            for base in bases.json().get("list", []):
                print(f"\n📁 Base: {base.get('title')}  (id={base.get('id')})")
                r = client.get(
                    f"{base_url}/api/v2/meta/bases/{base['id']}/tables", headers=_headers()
                )
                r.raise_for_status()
                for t in r.json().get("list", []):
                    name = table_name_for_title(t.get("title"))
                    tanda = f"✅ {name}" if name else "  "
                    print(f"   {tanda:26} {t.get('title'):24} {t.get('id')}")
                    if name:
                        pairs.append(f"{name}:{t.get('id')}")
            if pairs:
                print("\n📋 Salin ke .env (pilih baris dari base yang benar):\n")
                print(f'NOCODB_TABLE_IDS="{",".join(pairs)}"')
            else:
                print("\n⚠️  Belum ada tabel dol-schema. Jalankan --create-base.")
    except httpx.HTTPError as e:
        print(f"❌ Gagal menghubungi NocoDB di {base_url}: {e}")
        return 1
    return 0


def cmd_check() -> int:
    base_url = config.NOCODB_URL.rstrip("/")
    print(f"NOCODB_URL   : {base_url}")
    try:
        with httpx.Client(timeout=10.0) as client:
            client.get(f"{base_url}/api/v1/health")
        print("  ✅ server terjangkau")
    except httpx.HTTPError as e:
        print(f"  ❌ tidak terjangkau: {e}")
        return 1
    if not _require_token():
        return 1
    table_ids = config.nocodb_table_ids()
    if not table_ids:
        print("TABEL        : ❌ NOCODB_TABLE_IDS kosong — jalankan --list-tables")
        return 1
    gagal = 0
    with httpx.Client(timeout=15.0) as client:
        for name, tid in sorted(table_ids.items()):
            try:
                r = client.get(
                    f"{base_url}/api/v2/tables/{tid}/records",
                    headers=_headers(),
                    params={"limit": 1},
                )
                r.raise_for_status()
                jumlah = r.json().get("pageInfo", {}).get("totalRows", "?")
                kunci = "+".join(natural_key(table(name)) or ()) or "—"
                print(f"  ✅ {name:22} {tid}  ({jumlah} baris, kunci: {kunci})")
            except (httpx.HTTPError, KeyError) as e:
                print(f"  ❌ {name:22} {tid}  -> {e}")
                gagal += 1
    if gagal:
        print(f"\n{gagal} tabel bermasalah. Cek ulang tableId-nya lewat --list-tables.")
        return 1
    print("\n✅ Semua siap. Push bisa dijalankan.")
    return 0


def cmd_compare_schema() -> int:
    """Cocokkan tabel di NocoDB dengan dol-schema, kolom per kolom (judul, tipe, pilihan)."""
    if not _require_token():
        return 1
    spec = to_nocodb_fields()["tables"]
    ids = config.nocodb_table_ids()
    base_url = config.NOCODB_URL.rstrip("/")
    beda = 0
    with httpx.Client(timeout=15.0) as client:
        for name, t in spec.items():
            if name not in ids:
                print(f"  ❌ {name:22} tidak ada di NOCODB_TABLE_IDS")
                beda += 1
                continue
            r = client.get(f"{base_url}/api/v2/meta/tables/{ids[name]}", headers=_headers())
            r.raise_for_status()
            kolom = r.json()["columns"]
            got = {
                c["title"]: (
                    c["uidt"],
                    [o["title"] for o in (c.get("colOptions") or {}).get("options", [])],
                )
                for c in kolom
                if c["uidt"] not in _NOCODB_SYSTEM_UIDT | _LINK_UIDT
            }
            fk = _relasi(name)
            exp = {
                # Kolom FK dibuat NocoDB sebagai pasangan relasi Link, bertipe ForeignKey.
                f["title"]: (
                    "ForeignKey" if f["column_name"] in fk else f["uidt"],
                    list(f.get("options") or []),
                )
                for f in t["columns"]
            }
            induk_tertaut = {
                (c.get("colOptions") or {}).get("fk_related_model_id")
                for c in kolom
                if c["uidt"] in _LINK_UIDT and (c.get("colOptions") or {}).get("type") == "bt"
            }
            masalah = [
                f"relasi ke '{induk}' bukan Link (jalankan --create-base di base baru)"
                for induk in fk.values()
                if ids.get(induk) not in induk_tertaut
            ] + (
                [f"hilang di NocoDB: {k}" for k in exp.keys() - got.keys()]
                + [f"tidak ada di skema: {k}" for k in got.keys() - exp.keys()]
                + [
                    f"beda {k}: skema {exp[k]} vs NocoDB {got[k]}"
                    for k in exp.keys() & got.keys()
                    if exp[k] != got[k]
                ]
            )
            print(f"  {'✅' if not masalah else '❌'} {name:22} {len(exp):2} kolom")
            for m in masalah:
                print(f"       {m}")
            beda += bool(masalah)
    if beda:
        print(f"\n{beda} tabel berbeda dari dol-schema {SCHEMA_VERSION}.")
        return 1
    print(f"\n✅ {len(spec)} tabel NocoDB identik dengan dol-schema {SCHEMA_VERSION}.")
    return 0


def _kolom(client: httpx.Client, base_url: str, tid: str) -> dict[str, dict]:
    r = client.get(f"{base_url}/api/v2/meta/tables/{tid}", headers=_headers())
    r.raise_for_status()
    return {c["id"]: c for c in r.json()["columns"]}


def _buat_relasi(
    client: httpx.Client, base_url: str, anak: str, ids: dict[str, str], spec: dict
) -> None:
    """
    Jadikan setiap kolom FK tabel `anak` relasi Link (belongs-to) ke tabel induknya.

    NocoDB membuat kolom FK-nya sendiri untuk relasi; kolom itu diberi judul dol-schema
    ("ID SPH", ...) supaya pusher tetap mengisinya dengan Id induk seperti kolom Number
    biasa. Yang bertambah untuk PM: kolom Link berisi baris induk yang bisa diklik di
    tabel anak, dan daftar baris anak di tabel induk.

    NocoDB di basis data bawaannya TIDAK menegakkan relasi ini: FK ke Id yang tidak ada
    tetap diterima. Kebenaran relasi tetap dijaga pusher (validasi dol-schema).
    """
    judul_fk = {f["column_name"]: f for f in spec[anak]["columns"]}
    for kolom_fk, induk in _relasi(anak).items():
        sebelum_anak = _kolom(client, base_url, ids[anak])
        if any(
            c["uidt"] in _LINK_UIDT
            and (c.get("colOptions") or {}).get("type") == "bt"
            and (c.get("colOptions") or {}).get("fk_related_model_id") == ids[induk]
            for c in sebelum_anak.values()
        ):
            print(f"   ⏭️  {spec[anak]['title']} → {spec[induk]['title']}: relasi sudah ada")
            continue
        if any(c["title"] == judul_fk[kolom_fk]["title"] for c in sebelum_anak.values()):
            # Base lama: FK sudah berupa kolom Number berisi data. Mengubahnya di tempat
            # berisiko; buat base baru (make buat-base JUDUL="...").
            print(
                f"   ⚠️  {spec[anak]['title']}: '{judul_fk[kolom_fk]['title']}' sudah ada "
                "sebagai kolom biasa — relasi dilewati, buat base baru"
            )
            continue
        sebelum_induk = _kolom(client, base_url, ids[induk])
        r = client.post(
            f"{base_url}/api/v2/meta/tables/{ids[anak]}/columns",
            headers=_headers(),
            json={
                "uidt": "LinkToAnotherRecord",
                "title": spec[induk]["title"],
                "parentId": ids[induk],
                "childId": ids[anak],
                "type": "bt",
            },
        )
        r.raise_for_status()
        f = judul_fk[kolom_fk]
        baru_anak = _kolom(client, base_url, ids[anak])
        fk = next(
            c for i, c in baru_anak.items() if i not in sebelum_anak and c["uidt"] == "ForeignKey"
        )
        ubah = {"title": f["title"], "column_name": fk["column_name"], "uidt": "ForeignKey"}
        if f.get("description"):
            ubah["description"] = f["description"]
        client.patch(
            f"{base_url}/api/v2/meta/columns/{fk['id']}", headers=_headers(), json=ubah
        ).raise_for_status()
        # Kolom balik di tabel induk diberi nama tabel anaknya, bukan nama jamak buatan NocoDB.
        balik = next(
            c
            for i, c in _kolom(client, base_url, ids[induk]).items()
            if i not in sebelum_induk and c["uidt"] in _LINK_UIDT
        )
        client.patch(
            f"{base_url}/api/v2/meta/columns/{balik['id']}",
            headers=_headers(),
            json={"title": spec[anak]["title"], "uidt": balik["uidt"]},
        ).raise_for_status()
        print(f"   🔗 {spec[anak]['title']:22} → {spec[induk]['title']} ({f['title']})")


def cmd_create_base(title: str) -> int:
    """Buat base berisi tabel dol-schema yang berlaku. Aman diulang."""
    if not _require_token():
        return 1
    spec = to_nocodb_fields()["tables"]
    base_url = config.NOCODB_URL.rstrip("/")
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{base_url}/api/v2/meta/bases", headers=_headers())
            r.raise_for_status()
            base = next((b for b in r.json().get("list", []) if b.get("title") == title), None)
            if base is None:
                r = client.post(
                    f"{base_url}/api/v2/meta/bases", headers=_headers(), json={"title": title}
                )
                r.raise_for_status()
                base = r.json()
                print(f"📁 Base baru: {title}  (id={base['id']})")
            else:
                print(f"📁 Base sudah ada: {title}  (id={base['id']}) — dipakai ulang")

            tables_url = f"{base_url}/api/v2/meta/bases/{base['id']}/tables"
            r = client.get(tables_url, headers=_headers())
            r.raise_for_status()
            ada = {t["title"]: t["id"] for t in r.json().get("list", [])}

            ids = {}
            for name, t in spec.items():
                if t["title"] in ada:
                    ids[name] = ada[t["title"]]
                    print(f"   ⏭️  {t['title']:22} sudah ada")
                    continue
                # Kolom FK tidak dibuat di sini: kolom itu lahir dari relasi Link di bawah.
                fk = _relasi(name)
                r = client.post(
                    tables_url,
                    headers=_headers(),
                    json={
                        "table_name": name,
                        "title": t["title"],
                        "description": t["description"],
                        "columns": [
                            _column_spec(f) for f in t["columns"] if f["column_name"] not in fk
                        ],
                    },
                )
                if r.is_error:
                    print(f"   ❌ {name}: {r.status_code} {r.text[:300]}")
                    return 1
                ids[name] = r.json()["id"]
                print(f"   ✅ {t['title']:22} {len(t['columns']):2d} kolom  {ids[name]}")

            # Induk dulu dibuat semua (urutan spec = urutan model), baru relasinya.
            for name in spec:
                _buat_relasi(client, base_url, name, ids, spec)
    except httpx.HTTPError as e:
        print(f"❌ Gagal menghubungi NocoDB di {base_url}: {e}")
        return 1

    print("\n📋 Pasang di .env:\n")
    print(f"NOCODB_BASE_ID={base['id']}")
    print(f"NOCODB_TABLE_IDS={','.join(f'{n}:{t}' for n, t in ids.items())}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--list-tables", action="store_true", help="tampilkan base & tableId")
    ap.add_argument("--check", action="store_true", help="uji sambungan & pemetaan tabel")
    ap.add_argument(
        "--create-base",
        metavar="JUDUL",
        nargs="?",
        const=f"DOL Schema {SCHEMA_VERSION.split('-', 1)[1]}",
        help="buat base + tabel dol-schema (default judul: 'DOL Schema <versi>')",
    )
    ap.add_argument(
        "--compare-schema",
        action="store_true",
        help="cocokkan tabel NocoDB dengan dol-schema, kolom per kolom",
    )
    args = ap.parse_args()

    if args.compare_schema:
        sys.exit(cmd_compare_schema())
    if args.create_base:
        sys.exit(cmd_create_base(args.create_base))
    if args.list_tables:
        sys.exit(cmd_list_tables())
    if args.check:
        sys.exit(cmd_check())
    ap.print_help()


if __name__ == "__main__":
    main()
