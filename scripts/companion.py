#!/usr/bin/env python3
"""
Open ADE — CLI payload companion (skema dol-schema): kontrak dan SPH.

    python scripts/companion.py --tables                        # tabel & statusnya
    python scripts/companion.py --ddl [--write]                 # schema.sql PostgreSQL
    python scripts/companion.py --map FILE.extract.json         # hasilkan payload companion
    python scripts/companion.py --check FILE.companion.json     # validasi terhadap skema
    python scripts/companion.py --push FILE.companion.json --dry-run   # rencana kirim
    python scripts/companion.py --push FILE.companion.json              # kirim sungguhan

Dokumen BAST tidak dipetakan: tabel BAST masih usulan (ditunda) di dol-schema.
Skema & kamus data: ../dol-schema (generated/KAMUS_DATA.md).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.companion.payload import build_companion_payload, payload_note  # noqa: E402
from dol_schema import ALL_TABLES, generate_ddl, insert_order, validate_payload  # noqa: E402

OUT_DIR = ROOT / "storage" / "outputs" / "companion"


def cmd_ddl(write: bool) -> int:
    ddl = generate_ddl()
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / "schema.sql"
        path.write_text(ddl, encoding="utf-8")
        print(f"✅ {path}  ({len(ddl.splitlines())} baris)")
    else:
        print(ddl)
    return 0


def cmd_tables() -> int:
    print(f"{len(ALL_TABLES)} tabel — urutan insert: {' -> '.join(insert_order())}\n")
    for t in ALL_TABLES:
        penulis = "MANUSIA" if t.written_by == "human" else "mesin"
        print(
            f"  {t.name:21} {t.label:20} {t.status:8} {len(t.columns):2d} kolom  ditulis: {penulis}"
        )
    return 0


def _find_source_pdf(document_name: str) -> Path | None:
    """Cari berkas asli untuk menghitung sidik isinya. Bila tidak ketemu, kunci diambil dari
    run_info.document_id -- yang juga sidik isi berkas yang sama."""
    if not document_name:
        return None
    for folder in (ROOT / "sample_pdfs", ROOT / "storage" / "uploads"):
        if not folder.exists():
            continue
        hit = next((p for p in folder.rglob(document_name) if p.is_file()), None)
        if hit:
            return hit
    return None


def cmd_map(path: Path, pdf: str | None = None) -> int:
    result = json.loads(path.read_text(encoding="utf-8"))
    catatan = payload_note(result.get("document_type"))
    if catatan:
        print(f"❌ {path.name}: {catatan}")
        return 1
    md = ROOT / "storage" / "outputs" / "parsing" / path.name.replace(".extract.json", ".parse.md")
    if md.exists() and not result.get("markdown"):
        result["markdown"] = md.read_text(encoding="utf-8")

    src = Path(pdf) if pdf else _find_source_pdf(result.get("document_name") or "")
    try:
        payload = build_companion_payload(result, src)
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / path.name.replace(".extract.json", ".companion.json")
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"  kunci dokumen : {payload['document'][0]['content_hash']}")
    for t, rows in payload.items():
        print(f"  {t:21} {len(rows):3d} baris")
    print(f"\n✅ {out}")
    problems = validate_payload(payload)
    print(
        ("⚠️  " + str(len(problems)) + " masalah — jalankan --check untuk detail")
        if problems
        else "✅ payload lolos validasi"
    )
    return 0


def cmd_check(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = validate_payload(payload)
    if not problems:
        total = sum(len(v) for v in payload.values())
        print(f"✅ {total} baris / {len(payload)} tabel — semua sesuai skema")
        return 0
    print(f"❌ {len(problems)} masalah:")
    for p in problems:
        print(f"   {p}")
    return 1


def cmd_push(path: Path, dry_run: bool, allow_reviewed: bool) -> int:
    from app.companion.nocodb_push import CompanionPusher, CompanionPushError
    from app.config import config

    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        pusher = CompanionPusher(
            config.NOCODB_URL, config.NOCODB_API_TOKEN, config.nocodb_table_ids()
        )
        hasil = pusher.push(payload, dry_run=dry_run, allow_reviewed=allow_reviewed)
    except CompanionPushError as e:
        print(f"❌ {e}")
        return 1
    if dry_run:
        print(f"RENCANA (tidak ada yang dikirim) — {config.NOCODB_URL}\n")
        for t in hasil["urutan"]:
            print(f"  {t:21} {hasil['baris'][t]:3d} baris   {hasil['strategi'][t]}")
        for t, alasan in hasil["dilewati"].items():
            print(f"  {t:21}   —       dilewati: {alasan}")
        print("\n  field_review           —       tidak disentuh (milik PM)")
        return 0
    for t, s in hasil["tables"].items():
        print(f"  {t:21} {s}")
    print(
        f"\n✅ {hasil['inserted']} baris baru, {hasil['updated']} diperbarui, "
        f"{hasil['deleted']} baris basi dihapus"
    )
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ddl", action="store_true", help="cetak DDL PostgreSQL (tabel berlaku)")
    ap.add_argument("--write", action="store_true", help="dengan --ddl: simpan ke file")
    ap.add_argument("--tables", action="store_true", help="ringkas tabel")
    ap.add_argument("--map", metavar="FILE", help="petakan *.extract.json (kontrak atau SPH)")
    ap.add_argument("--check", metavar="FILE", help="validasi *.companion.json")
    ap.add_argument("--pdf", metavar="PATH", help="berkas asli untuk kunci dokumen")
    ap.add_argument("--push", metavar="FILE", help="kirim *.companion.json ke NocoDB")
    ap.add_argument("--dry-run", action="store_true", help="dengan --push: hanya rencana")
    ap.add_argument(
        "--timpa-yang-direview",
        action="store_true",
        help="dengan --push: tetap kirim walau dokumen sudah punya Keputusan PM "
        "(hanya atas permintaan PM)",
    )
    a = ap.parse_args()
    if a.ddl:
        sys.exit(cmd_ddl(a.write))
    if a.tables:
        sys.exit(cmd_tables())
    if a.map:
        sys.exit(cmd_map(Path(a.map), a.pdf))
    if a.check:
        sys.exit(cmd_check(Path(a.check)))
    if a.push:
        sys.exit(cmd_push(Path(a.push), a.dry_run, a.timpa_yang_direview))
    ap.print_help()


if __name__ == "__main__":
    main()
