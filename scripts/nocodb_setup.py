#!/usr/bin/env python3
"""
Open ADE — penyiapan & uji sambungan NocoDB untuk skema dol-schema.

    python scripts/nocodb_setup.py --create-base ["DOL Schema 2026.10.5"]
        Buat base berisi tabel dol-schema yang BERLAKU (judul Indonesia) dengan kolom FK
        sebagai relasi Link, lalu cetak NOCODB_TABLE_IDS-nya. Aman diulang; base lain
        tidak disentuh.

    python scripts/nocodb_setup.py --create-views ["DOL Schema 2026.10.5"]
        Buat tampilan siap pakai untuk PM di base itu (antrean verifikasi, harga, dokumen
        masuk, rincian per baris). Aman diulang: tampilan yang sudah ada tidak diubah.

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
import os
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
                if c["uidt"] not in _NOCODB_SYSTEM_UIDT | _LINK_UIDT | _UIDT_TAMPILAN
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


# Kolom tampilan untuk PM: tidak menyimpan data, dihitung NocoDB dari kolom dol-schema
# (Formula, Lookup, Rollup, Button). Judulnya hanya ada di NocoDB, jadi --compare-schema
# melewatinya. Rumus menulis kolom dol-schema sebagai {{nama_teknis}} (diganti judul dari
# skema) dan kolom tampilan lain sebagai {Judul}. NocoDB di basis data bawaannya (SQLite)
# tidak punya FIND, SUBSTITUTE, VALUE, maupun regex, dan membandingkan dengan `==`; rumus di
# bawah hanya memakai fungsi yang diuji tersedia (diuji di NocoDB 2026.08, 18 Okt 2026).
_UIDT_TAMPILAN = {"Formula", "Rollup", "Lookup", "Button"}

_NF = "{{field_path}}"
_IDX = f'(MID({_NF}, SEARCH({_NF}, "[") + 1, SEARCH({_NF}, "]") - SEARCH({_NF}, "[") - 1) * 1) + 1'
_ITEM = f'OR(LEFT({_NF}, 17) == "List Item/Barang[", LEFT({_NF}, 23) == "Daftar Penawaran Harga[")'
# "List Item/Barang[1].Harga Satuan" -> "Baris 2 · Harga Satuan"; daftar lain memakai namanya
# ("Daftar Penandatangan[2].Jabatan" -> "Daftar Penandatangan 3 · Jabatan"). Indeks mulai 0.
_RUMUS_FIELD = (
    f'IF(SEARCH({_NF}, "].") > 0, '
    f'CONCAT(IF({_ITEM}, "Baris ", CONCAT(LEFT({_NF}, SEARCH({_NF}, "[") - 1), " ")), {_IDX}, '
    f'" · ", MID({_NF}, SEARCH({_NF}, "].") + 2, LEN({_NF}))), '
    f'IF(SEARCH({_NF}, "[") > 0, CONCAT(LEFT({_NF}, SEARCH({_NF}, "[") - 1), " ", {_IDX}), '
    f'REPLACE({_NF}, ".", " · ")))'
)
# Bagian dokumen tempat field berada; angka di depan menjaga urutan kelompok di layar.
_L = f"LOWER({_NF})"
_RUMUS_BAGIAN = (
    f'IF(LEFT({_NF}, 13) == "Pihak Pertama", "2 · Pihak Pertama", '
    f'IF(LEFT({_NF}, 11) == "Pihak Kedua", "3 · Pihak Kedua", '
    f'IF(OR(LEFT({_NF}, 6) == "Vendor", LEFT({_NF}, 12) == "Tujuan Surat"), "2 · Pihak", '
    f'IF({_ITEM}, "5 · Rincian harga", '
    f'IF(SEARCH({_NF}, "[") > 0, "7 · Daftar lain", '
    f'IF(OR(SEARCH({_L}, "total") > 0, SEARCH({_L}, "ppn") > 0, SEARCH({_L}, "terbilang") > 0), '
    f'"4 · Nilai dan pajak", '
    f'IF(OR(SEARCH({_L}, "bank") > 0, SEARCH({_L}, "rekening") > 0), "6 · Rekening", '
    f'"1 · Data utama")))))))'
)
_ST = "{{system_status}}"
_RUMUS_BERISIKO = (
    f'IF(OR({_ST} == "tidak_ada_di_dokumen", {_ST} == "bertentangan", '
    f'{_ST} == "perlu_dicek"), 1, 0)'
)
# Urutan antrean menurut 08_UI_UX.md §2: yang paling mungkin salah di atas.
_RUMUS_URUTAN_RISIKO = (
    f'SWITCH({_ST}, "bertentangan", 1, "tidak_ada_di_dokumen", 2, "perlu_dicek", 3, '
    f'"bukti_cukup", 4, "bukti_kuat", 5, 6)'
)


def _rumus_cek_hitung() -> str:
    """Penanda di grid verifikasi; bukan nilai yang disimpan. Periode bulanan ikut dihitung."""
    v, h, j, p = "{{quantity}}", "{{unit_price}}", "{{line_total}}", "{{period}}"
    return (
        f'IF(OR(ISBLANK({h}), ISBLANK({j})), "", '
        f"IF(OR(ROUND({v} * {h}, 0) == ROUND({j}, 0), ROUND({v} * {h} * ({p} * 1), 0) == "
        f'ROUND({j}, 0)), "cocok", "cek hitungan"))'
    )


def _rumus_tombol(form: str, nama_field: str, nilai: str, dokumen: str, relasi: str) -> str:
    """
    URL form Keputusan PM yang sudah terisi: field, nilai yang dilihat PM, dokumen, baris
    yang diputuskan, dan waktu (UTC, ditandai +00:00 supaya form menampilkan jam setempat).
    `nama_field` dan `nilai` sudah berupa ekspresi ter-encode URL. `relasi` = judul kolom
    relasi di form ("Hasil Ekstraksi", "Rincian SPH", ...).

    Dua batas NocoDB di SQLite: CONCAT datar sepanjang ini ditolak sebagai "Invalid URL
    Formula", jadi CONCAT-nya bersarang; dan URLENCODE diterjemahkan menjadi REPLACE
    bertumpuk, sehingga URLENCODE atas teks gabungan yang panjang membuat SQLite gagal
    membaca tabel ("parser stack overflow"). URLENCODE hanya dipakai untuk satu kolom teks.
    """
    q = relasi.replace(" ", "+")
    return (
        f'CONCAT(CONCAT("{form}?Nama+Field=", {nama_field}), '
        f'CONCAT("&Nilai+Sistem+Saat+Diperiksa=", {nilai}), '
        f'CONCAT("&Dokumen=", {dokumen}), CONCAT("&{q}=", RECORD_ID()), '
        f'CONCAT("&Waktu+Diperiksa=", URLENCODE(NOW()), "%2B00%3A00"))'
    )


def _nilai_baris() -> str:
    """'2 Unit x 5000000 = 10000000', ter-encode; angka tidak perlu di-encode."""
    return (
        'CONCAT({{quantity}}, "+", IF(ISBLANK({{unit}}), "", URLENCODE({{unit}})), "+x+", '
        '{{unit_price}}, "+%3D+", {{line_total}})'
    )


# Urutan penting: kolom yang dirujuk kolom lain dibuat lebih dulu. "@tabel" = relasi Link ke
# tabel itu. Tombol butuh alamat form Keputusan PM ({FORM}), dibuat sebelum kolom ini.
KOLOM_TAMPILAN: list[dict] = [
    {"tabel": "extracted_field", "judul": "Field", "rumus": _RUMUS_FIELD},
    {"tabel": "extracted_field", "judul": "Bagian", "rumus": _RUMUS_BAGIAN},
    {"tabel": "extracted_field", "judul": "Berisiko", "rumus": _RUMUS_BERISIKO},
    {"tabel": "extracted_field", "judul": "Urutan Risiko", "rumus": _RUMUS_URUTAN_RISIKO},
    {"tabel": "extracted_field", "judul": "Keputusan", "lookup": ("@field_review", "decision")},
    {
        "tabel": "extracted_field",
        "judul": "Jumlah Keputusan",
        "rollup": ("@field_review", "decision", "count"),
    },
    {
        "tabel": "extracted_field",
        "judul": "Putuskan",
        "tombol": _rumus_tombol(
            "{FORM}",
            "URLENCODE({{field_path}})",
            'IF(ISBLANK({{ai_value_text}}), "", URLENCODE({{ai_value_text}}))',
            "{{document_id}}",
            "@extracted_field",
        ),
    },
    {
        "tabel": "document",
        "judul": "Jumlah Field",
        "rollup": ("@extracted_field", "field_path", "count"),
    },
    {
        "tabel": "document",
        "judul": "Sudah Diputuskan",
        "rollup": ("@field_review", "decision", "count"),
    },
    {
        "tabel": "document",
        "judul": "Perlu Dicek",
        "rollup": ("@extracted_field", "Berisiko", "sum"),
    },
    {
        "tabel": "document",
        "judul": "Progres",
        "rumus": "IF({Jumlah Field} == 0, 0, ROUND({Sudah Diputuskan} * 100 / {Jumlah Field}, 0))",
    },
    {
        "tabel": "document",
        "judul": "Status Pemeriksaan",
        "rumus": 'IF({Sudah Diputuskan} == 0, "belum diperiksa", '
        'IF({Sudah Diputuskan} >= {Jumlah Field}, "selesai", "sedang dicek"))',
    },
]
# Nama Field keputusan per baris: "Daftar Penawaran Harga[2]", ditulis langsung ter-encode.
for _tabel, _induk, _jalur in (
    ("sph_item", "sph", "Daftar+Penawaran+Harga"),
    ("contract_item", "contract", "List+Item%2FBarang"),
):
    KOLOM_TAMPILAN += [
        {"tabel": _tabel, "judul": "Dokumen Asal", "lookup": (f"@{_induk}", "document_id")},
        {"tabel": _tabel, "judul": "Cek Hitung", "rumus": _rumus_cek_hitung()},
        {"tabel": _tabel, "judul": "Keputusan", "lookup": ("@field_review", "decision")},
        {
            "tabel": _tabel,
            "judul": "Jumlah Keputusan",
            "rollup": ("@field_review", "decision", "count"),
        },
        {
            "tabel": _tabel,
            "judul": "Cek Baris",
            "tombol": _rumus_tombol(
                "{FORM}",
                f'CONCAT("{_jalur}%5B", {{{{line_no}}}} - 1, "%5D")',
                _nilai_baris(),
                "{Dokumen Asal}",
                f"@{_tabel}",
            ),
        },
    ]

# Warna pilihan nilai (08_UI_UX.md §2). Pewarnaan baris butuh lisensi; warna pilihan tidak.
WARNA_PILIHAN = {
    "extracted_field.system_status": {
        "bertentangan": "#ff7a7a",
        "tidak_ada_di_dokumen": "#ffb3b3",
        "perlu_dicek": "#ffe08a",
        "bukti_cukup": "#c8f0c0",
        "bukti_kuat": "#8fd68a",
        "kosong": "#e0e0e0",
    },
    "field_review.decision": {"benar": "#8fd68a", "dikoreksi": "#ffe08a", "ditolak": "#ff7a7a"},
}

# Tampilan siap pakai untuk PM. Kolom dol-schema ditulis dengan nama TEKNIS; judul yang tampil
# di NocoDB diambil dari skema. Kolom tampilan (KOLOM_TAMPILAN) ditulis dengan judulnya.
#   kolom  : urutan kolom yang tampil; kolom lain disembunyikan. Kolom FK (mis. document_id)
#            ditampilkan sebagai relasi Link ke tabel induknya, yang bisa diklik PM.
#   filter : (kolom, operator NocoDB, nilai). Baris kedua dst. digabung dengan `gabung`.
#   urut   : (kolom, asc|desc). "_dibuat" = waktu baris dibuat (kolom sistem NocoDB).
#   kelompok: kolom pengelompokan, berurutan. kunci: tampilan dikunci (PM tetap bisa
#            mengisi data; hanya susunan tampilannya yang tidak berubah tanpa sengaja).
_STATUS_BERISIKO = "tidak_ada_di_dokumen,bertentangan,perlu_dicek"
_BAGIAN_RINCIAN, _BAGIAN_NILAI = "5 · Rincian harga", "4 · Nilai dan pajak"
_KOLOM_ANTREAN = [
    "document_id",
    "Field",
    "ai_value_text",
    "system_status",
    "status_reason",
    "evidence_page",
    "evidence_quote",
    "Keputusan",
    "Putuskan",
]
_KOLOM_RINCIAN = [
    "line_no",
    "description",
    "quantity",
    "unit",
    "period",
    "unit_price",
    "line_total",
    "charge_type",
    "Cek Hitung",
    "Keputusan",
    "Cek Baris",
]
TAMPILAN_PM: list[dict] = [
    {
        # Titik masuk PM: satu baris per dokumen dengan progres pemeriksaannya.
        "tabel": "document",
        "judul": "Beranda PM",
        "kolom": [
            "source_filename",
            "doc_type",
            "Status Pemeriksaan",
            "Progres",
            "Perlu Dicek",
            "Sudah Diputuskan",
            "Jumlah Field",
            "parse_quality",
            "validation_notes",
        ],
        "urut": [("_dibuat", "desc")],
        "kunci": True,
    },
    {
        # Yang paling berisiko di atas, per dokumen dan per bagian; field yang sudah
        # diputuskan PM hilang dari antrean. Rincian item diperiksa per baris di grid
        # verifikasi, jadi tidak diulang di sini (SPH 30 item = ratusan baris antrean).
        "tabel": "extracted_field",
        "judul": "Antrean PM",
        "kolom": _KOLOM_ANTREAN,
        "filter": [
            ("system_status", "anyof", _STATUS_BERISIKO),
            ("Jumlah Keputusan", "eq", "0"),
            ("Bagian", "neq", _BAGIAN_RINCIAN),
        ],
        "kelompok": ["document_id", "Bagian"],
        "urut": [("Urutan Risiko", "asc"), ("confidence", "asc")],
        "kunci": True,
    },
    {
        # Briefing hlm. 20: harga selalu diverifikasi manusia, apa pun status buktinya.
        # Subtotal, PPN, total, dan terbilang di sini; harga per item di grid verifikasi.
        # Dulu disaring `Nama Field like "Harga"`, yang ikut menangkap semua field SPH karena
        # nama daftarnya "Daftar Penawaran Harga[...]" (Merek pun masuk).
        "tabel": "extracted_field",
        "judul": "Harga Wajib Dicek",
        "kolom": _KOLOM_ANTREAN,
        "filter": [("Bagian", "eq", _BAGIAN_NILAI)],
        "kelompok": ["document_id"],
        "urut": [("field_path", "asc")],
        "kunci": True,
    },
    {
        "tabel": "extracted_field",
        "judul": "Semua Field per Dokumen",
        "kolom": _KOLOM_ANTREAN,
        "kelompok": ["document_id", "Bagian"],
        "urut": [("evidence_page", "asc")],
        "kunci": True,
    },
    {
        # Isi Dokumen (markdown utuh) disembunyikan: satu sel sepanjang itu membuat grid
        # tidak terbaca. PM membukanya lewat baris yang diperluas.
        "tabel": "document",
        "judul": "Dokumen Masuk",
        "kolom": ["source_filename", "doc_type", "page_count", "parse_quality", "validation_notes"],
        "urut": [("_dibuat", "desc")],
    },
    {
        # Grid verifikasi per baris (briefing hlm. 14, Bulan 3): satu baris penawaran = satu
        # baris grid, dengan tombol yang mencatat keputusan untuk seluruh baris itu.
        "tabel": "sph_item",
        "judul": "Verifikasi SPH per Baris",
        "kolom": ["sph_id", *_KOLOM_RINCIAN[:2], "brand", *_KOLOM_RINCIAN[2:]],
        "kelompok": ["sph_id"],
        "urut": [("line_no", "asc")],
        "kunci": True,
    },
    {
        "tabel": "contract_item",
        "judul": "Verifikasi Kontrak per Baris",
        "kolom": ["contract_id", *_KOLOM_RINCIAN],
        "kelompok": ["contract_id"],
        "urut": [("line_no", "asc")],
        "kunci": True,
    },
    {
        "tabel": "field_review",
        "judul": "Riwayat Keputusan",
        "kolom": [
            "document_id",
            "field_path",
            "decision",
            "final_value_text",
            "reviewer_note",
            "reviewed_by",
            "reviewed_at",
        ],
        "urut": [("reviewed_at", "desc")],
    },
]

# Form tempat PM mencatat keputusan. Dibagikan (tautan form) supaya tombol Putuskan dan Cek
# Baris bisa membukanya dalam keadaan terisi lewat URL; Nama Field dan Nilai Sistem tetap
# bisa dibetulkan PM. Keputusan dan nama pemeriksa wajib diisi.
FORM_KEPUTUSAN = {
    "tabel": "field_review",
    "judul": "Catat Keputusan",
    "keterangan": "Pilih benar, dikoreksi (isi Nilai Final), atau ditolak. Field dan nilai "
    "sistem sudah terisi dari baris yang Anda buka.",
    "kolom": [
        "field_path",
        "reviewed_ai_value_text",
        "decision",
        "final_value_text",
        "reviewer_note",
        "reviewed_by",
        "reviewed_at",
        "document_id",
        "extracted_field_id",
        "sph_item_id",
        "contract_item_id",
    ],
    "wajib": ["decision", "reviewed_by"],
}


class _Kolom:
    """Pencari kolom NocoDB satu tabel menurut nama teknis dol-schema atau judul tampilan."""

    def __init__(self, client: httpx.Client, base_url: str, tid: str, nama: str, spec: dict):
        self.daftar = list(_kolom(client, base_url, tid).values())
        self.per_judul = {c["title"]: c for c in self.daftar}
        self.judul_skema = {f["column_name"]: f["title"] for f in spec[nama]["columns"]}
        self.fk = _relasi(nama)
        self.spec = spec

    def data(self, nama: str) -> dict:
        """Kolom berisi nilai: untuk filter, urut, dan rumus (kolom FK = angka Id induk)."""
        if nama == "_dibuat":
            return next(c for c in self.daftar if c["uidt"] == "CreatedTime")
        if nama.startswith("@"):
            return self.per_judul[self.spec[nama[1:]]["title"]]
        return self.per_judul[self.judul_skema.get(nama, nama)]

    def tampil(self, nama: str) -> dict:
        """Kolom yang dilihat PM: kolom FK diganti relasi Link ke induknya bila ada."""
        if nama in self.fk:
            return self.per_judul.get(self.spec[self.fk[nama]]["title"]) or self.data(nama)
        return self.data(nama)


def _rumus(teks: str, nama: str, spec: dict, form: str) -> str:
    """{{nama_teknis}} -> {Judul Skema}; {FORM} -> alamat form Keputusan PM."""
    judul = {f["column_name"]: f["title"] for f in spec[nama]["columns"]}
    for teknis, j in judul.items():
        teks = teks.replace("{{" + teknis + "}}", "{" + j + "}")
    return teks.replace("{FORM}", form)


def _buat_kolom_tampilan(
    client: httpx.Client, base_url: str, ids: dict[str, str], spec: dict, form: str
) -> int:
    dibuat = 0
    for k in KOLOM_TAMPILAN:
        nama, tid = k["tabel"], ids[k["tabel"]]
        kol = _Kolom(client, base_url, tid, nama, spec)
        ada = kol.per_judul.get(k["judul"])
        body: dict = {"title": k["judul"]}
        if "rumus" in k:
            body |= {"uidt": "Formula", "formula_raw": _rumus(k["rumus"], nama, spec, form)}
        elif "tombol" in k:
            body |= {
                "uidt": "Button",
                "type": "url",
                "formula_raw": _rumus(k["tombol"], nama, spec, form).replace(
                    f"@{nama}", spec[nama]["title"].replace(" ", "+")
                ),
                "label": k["judul"],
                "color": "brand",
                "theme": "light",
            }
        else:
            relasi, sasaran, *fungsi = k.get("lookup") or k["rollup"]
            anak = relasi[1:]
            kol_anak = _Kolom(client, base_url, ids[anak], anak, spec)
            body |= {
                "uidt": "Lookup" if "lookup" in k else "Rollup",
                "fk_relation_column_id": kol.data(relasi)["id"],
                ("fk_lookup_column_id" if "lookup" in k else "fk_rollup_column_id"): kol_anak.data(
                    sasaran
                )["id"],
            }
            if fungsi:
                body["rollup_function"] = fungsi[0]
        if ada is not None:
            # Kolom hitungan buatan skrip ini: rumus yang diperbaiki ikut terpasang saat
            # dijalankan ulang. Lookup/Rollup tidak berubah bentuk, jadi dibiarkan.
            lama = (ada.get("colOptions") or {}).get("formula_raw")
            if "formula_raw" in body and lama is not None and lama != body["formula_raw"]:
                client.patch(
                    f"{base_url}/api/v2/meta/columns/{ada['id']}", headers=_headers(), json=body
                ).raise_for_status()
            continue
        r = client.post(
            f"{base_url}/api/v2/meta/tables/{tid}/columns", headers=_headers(), json=body
        )
        if r.is_error:
            judul_tabel = spec[nama]["title"]
            print(f"   ❌ kolom '{k['judul']}' di {judul_tabel}: {r.status_code} {r.text[:200]}")
        r.raise_for_status()
        dibuat += 1
    return dibuat


def _warnai_pilihan(client: httpx.Client, base_url: str, ids: dict[str, str], spec: dict) -> None:
    for kunci, warna in WARNA_PILIHAN.items():
        nama, teknis = kunci.split(".")
        c = _Kolom(client, base_url, ids[nama], nama, spec).data(teknis)
        opsi = [
            {**o, "color": warna.get(o["title"], o.get("color"))}
            for o in c["colOptions"]["options"]
        ]
        client.patch(
            f"{base_url}/api/v2/meta/columns/{c['id']}",
            headers=_headers(),
            json={
                "title": c["title"],
                "column_name": c["column_name"],
                "uidt": c["uidt"],
                "colOptions": {"options": opsi},
            },
        ).raise_for_status()


def _views(client: httpx.Client, base_url: str, tid: str) -> list[dict]:
    r = client.get(f"{base_url}/api/v2/meta/tables/{tid}/views", headers=_headers())
    r.raise_for_status()
    return r.json().get("list", [])


def _form_keputusan(client: httpx.Client, base_url: str, ids: dict[str, str], spec: dict) -> str:
    """Buat (atau pakai ulang) form Keputusan PM yang dibagikan; kembalikan alamatnya."""
    f = FORM_KEPUTUSAN
    tid = ids[f["tabel"]]
    ada = next((v for v in _views(client, base_url, tid) if v["title"] == f["judul"]), None)
    if ada is None:
        r = client.post(
            f"{base_url}/api/v2/meta/tables/{tid}/forms",
            headers=_headers(),
            json={"title": f["judul"]},
        )
        r.raise_for_status()
        vid = r.json()["id"]
        client.patch(
            f"{base_url}/api/v2/meta/forms/{vid}",
            headers=_headers(),
            json={
                "heading": f["judul"],
                "subheading": f["keterangan"],
                "submit_another_form": False,
            },
        ).raise_for_status()
        kol = _Kolom(client, base_url, tid, f["tabel"], spec)
        urutan = {kol.tampil(k)["id"]: i for i, k in enumerate(f["kolom"], start=1)}
        wajib = {kol.tampil(k)["id"] for k in f["wajib"]}
        r = client.get(f"{base_url}/api/v2/meta/views/{vid}/columns", headers=_headers())
        r.raise_for_status()
        for vc in r.json().get("list", []):
            if vc["fk_column_id"] not in urutan:
                continue
            client.patch(
                f"{base_url}/api/v2/meta/form-columns/{vc['id']}",
                headers=_headers(),
                json={
                    "show": True,
                    "order": urutan[vc["fk_column_id"]],
                    "required": vc["fk_column_id"] in wajib,
                },
            ).raise_for_status()
    else:
        vid = ada["id"]
    r = client.post(f"{base_url}/api/v2/meta/views/{vid}/share", headers=_headers())
    r.raise_for_status()
    uuid = r.json().get("uuid")
    # Tanpa preFillEnabled, parameter URL diabaikan dan form terbuka kosong.
    client.patch(
        f"{base_url}/api/v2/meta/views/{vid}/share",
        headers=_headers(),
        json={
            "meta": {
                "allowCSVDownload": False,
                "preFillEnabled": True,
                "preFilledMode": "default",
                "language": "id",
            }
        },
    ).raise_for_status()
    publik = (os.environ.get("NOCODB_PUBLIC_URL") or base_url).rstrip("/")
    return f"{publik}/nc/form/{uuid}"


def _isi_tampilan(client: httpx.Client, base_url: str, tid: str, t: dict, spec: dict) -> None:
    """Buat satu tampilan grid di tabel `tid` lalu pasang filter, urutan, kelompok, kolomnya."""
    kol = _Kolom(client, base_url, tid, t["tabel"], spec)

    r = client.post(
        f"{base_url}/api/v2/meta/tables/{tid}/grids", headers=_headers(), json={"title": t["judul"]}
    )
    r.raise_for_status()
    vid = r.json()["id"]

    for i, (nama, op, nilai) in enumerate(t.get("filter", [])):
        body = {"fk_column_id": kol.data(nama)["id"], "comparison_op": op, "value": nilai}
        if i:
            body["logical_op"] = t.get("gabung", "and")
        client.post(
            f"{base_url}/api/v2/meta/views/{vid}/filters", headers=_headers(), json=body
        ).raise_for_status()
    for nama, arah in t.get("urut", []):
        client.post(
            f"{base_url}/api/v2/meta/views/{vid}/sorts",
            headers=_headers(),
            json={"fk_column_id": kol.data(nama)["id"], "direction": arah},
        ).raise_for_status()

    # Kolom yang diminta tampil berurutan; kolom lain disembunyikan. Kolom sistem (Id, waktu,
    # pengubah) dibiarkan: NocoDB sudah menyembunyikannya secara bawaan.
    urutan = {kol.tampil(k)["id"]: i for i, k in enumerate(t["kolom"], start=1)}
    kelompok = {kol.tampil(k)["id"]: i for i, k in enumerate(t.get("kelompok", []), start=1)}
    sistem = {c["id"] for c in kol.daftar if c["uidt"] in _NOCODB_SYSTEM_UIDT}
    r = client.get(f"{base_url}/api/v2/meta/views/{vid}/columns", headers=_headers())
    r.raise_for_status()
    for vc in r.json().get("list", []):
        if vc["fk_column_id"] in sistem:
            continue
        ubah = (
            {"show": True, "order": urutan[vc["fk_column_id"]]}
            if vc["fk_column_id"] in urutan
            else {"show": False}
        )
        client.patch(
            f"{base_url}/api/v2/meta/views/{vid}/columns/{vc['id']}", headers=_headers(), json=ubah
        ).raise_for_status()
        if vc["fk_column_id"] in kelompok:
            # Pengelompokan hanya tersimpan lewat grid-columns; PATCH views/{id}/columns
            # menjawab 200 tetapi mengabaikannya.
            client.patch(
                f"{base_url}/api/v2/meta/grid-columns/{vc['id']}",
                headers=_headers(),
                json={
                    "group_by": True,
                    "group_by_order": kelompok[vc["fk_column_id"]],
                    "group_by_sort": "asc",
                },
            ).raise_for_status()
    if t.get("kunci"):
        client.patch(
            f"{base_url}/api/v2/meta/views/{vid}", headers=_headers(), json={"lock_type": "locked"}
        ).raise_for_status()


def buat_tampilan(client: httpx.Client, base_url: str, base_id: str, spec: dict) -> int:
    """
    Siapkan base `base_id` untuk PM: warna pilihan, form Keputusan PM, kolom tampilan, lalu
    TAMPILAN_PM. Mengembalikan jumlah tampilan yang dibuat.

    Kolom dan tampilan yang judulnya sudah ada dilewati, tidak ditimpa: PM boleh menyesuaikan
    filter dan lebar kolomnya sendiri, dan menjalankan ulang perintah ini tidak boleh
    menghapusnya.
    """
    r = client.get(f"{base_url}/api/v2/meta/bases/{base_id}/tables", headers=_headers())
    r.raise_for_status()
    id_tabel = {t["title"]: t["id"] for t in r.json().get("list", [])}
    ids = {nama: id_tabel[s["title"]] for nama, s in spec.items() if s["title"] in id_tabel}
    kurang = [spec[n]["title"] for n in {t["tabel"] for t in TAMPILAN_PM} if n not in ids]
    if kurang:
        print(f"   ❌ tabel {kurang} tidak ada di base ini — jalankan --create-base")
        return 0

    _warnai_pilihan(client, base_url, ids, spec)
    form = _form_keputusan(client, base_url, ids, spec)
    print(f"   🔗 form Keputusan PM: {form}")
    n = _buat_kolom_tampilan(client, base_url, ids, spec, form)
    print(f"   ✅ {n} kolom tampilan dibuat")

    dibuat = 0
    for t in TAMPILAN_PM:
        tid = ids[t["tabel"]]
        if any(v["title"] == t["judul"] for v in _views(client, base_url, tid)):
            print(f"   ⏭️  {spec[t['tabel']]['title']}: '{t['judul']}' sudah ada")
            continue
        _isi_tampilan(client, base_url, tid, t, spec)
        print(f"   ✅ {spec[t['tabel']]['title']:18} '{t['judul']}'  ({len(t['kolom'])} kolom)")
        dibuat += 1
    return dibuat


def cmd_create_views(title: str) -> int:
    """Buat tampilan PM di base berjudul `title`. Aman diulang."""
    if not _require_token():
        return 1
    base_url = config.NOCODB_URL.rstrip("/")
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.get(f"{base_url}/api/v2/meta/bases", headers=_headers())
            r.raise_for_status()
            base = next((b for b in r.json().get("list", []) if b.get("title") == title), None)
            if base is None:
                print(f"❌ Base '{title}' tidak ada. Buat dulu: --create-base")
                return 1
            print(f"📁 Base: {title}  (id={base['id']})")
            dibuat = buat_tampilan(client, base_url, base["id"], to_nocodb_fields()["tables"])
    except httpx.HTTPError as e:
        print(f"❌ Gagal membuat tampilan di {base_url}: {e}")
        return 1
    print(f"\n✅ {dibuat} tampilan dibuat, {len(TAMPILAN_PM) - dibuat} dilewati.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--list-tables", action="store_true", help="tampilkan base & tableId")
    ap.add_argument("--check", action="store_true", help="uji sambungan & pemetaan tabel")
    _judul_bawaan = f"DOL Schema {SCHEMA_VERSION.split('-', 1)[1]}"
    ap.add_argument(
        "--create-base",
        metavar="JUDUL",
        nargs="?",
        const=_judul_bawaan,
        help="buat base + tabel dol-schema (default judul: 'DOL Schema <versi>')",
    )
    ap.add_argument(
        "--create-views",
        metavar="JUDUL",
        nargs="?",
        const=_judul_bawaan,
        help="buat tampilan siap pakai untuk PM di base itu (default: 'DOL Schema <versi>')",
    )
    ap.add_argument(
        "--compare-schema",
        action="store_true",
        help="cocokkan tabel NocoDB dengan dol-schema, kolom per kolom",
    )
    args = ap.parse_args()

    if args.compare_schema:
        sys.exit(cmd_compare_schema())
    if args.create_views:
        sys.exit(cmd_create_views(args.create_views))
    if args.create_base:
        sys.exit(cmd_create_base(args.create_base))
    if args.list_tables:
        sys.exit(cmd_list_tables())
    if args.check:
        sys.exit(cmd_check())
    ap.print_help()


if __name__ == "__main__":
    main()
