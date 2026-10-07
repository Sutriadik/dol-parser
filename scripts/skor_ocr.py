#!/usr/bin/env python3
"""
Open ADE — Menilai hasil bench_ocr.py terhadap berkas golden, dalam bentuk kolom Excel
perbandingan OCR.

Definisi kolom mengikuti panduan pengisian di lembar Excel, bukan karangan sendiri:

  Total Field Diharapkan = field golden yang MEMANG ADA isinya di dokumen asli.
                           Field yang di dokumen aslinya kosong tidak ikut dihitung,
                           supaya mesin OCR tidak dapat nilai gratis dari field kosong.
  Field Terisi           = dari field itu, berapa yang diisi pipeline (apa pun isinya).
  Field Terisi Benar     = dari yang terisi, berapa yang isinya benar setelah dicocokkan
                           ke dokumen asli (exact atau setara secara makna).
  Completeness Rate      = Field Terisi / Total Field Diharapkan
  Accuracy Rate          = Field Terisi Benar / Total Field Diharapkan

Dicatat juga "field terisi padahal seharusnya kosong" (spurious). Itu tidak masuk rumus
di atas, tapi wajib dilaporkan: field karangan lebih berbahaya daripada field kosong,
karena kosong terlihat oleh PM sedangkan karangan tidak.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from scripts.bench_ocr import BENCH, DOCS, ENGINES, HASIL  # noqa: E402
except ImportError:
    from bench_ocr import BENCH, DOCS, ENGINES, HASIL  # noqa: E402
from eval.run_eval import compare, flatten, is_empty  # noqa: E402

GOLDEN = ROOT / "eval" / "golden"
BENAR = {"exact", "lenient"}


def nilai_satu(doc_key: str, engine: str) -> dict | None:
    # Dibaca dari berkas *.extract.json yang ditulis pipeline, bukan dari nilai kembalian
    # process_full: yang dikembalikan memakai kunci "extracted" berisi objek pydantic,
    # sedangkan berkas di disk sudah rapi dengan kunci "data" dan alias yang benar.
    stem = Path(DOCS[doc_key]).stem
    f = BENCH / engine / "extraction" / f"{stem}.extract.json"
    g = GOLDEN / (stem + ".json")
    if not f.exists() or not g.exists():
        return None
    pred = json.loads(f.read_text(encoding="utf-8"))
    golden = json.loads(g.read_text(encoding="utf-8"))
    flat = flatten(pred.get("data") or {})

    diharapkan = terisi = benar = spurious = 0
    salah_kritis = []
    kritis = set(golden.get("critical_fields", []))
    for field, harapan in golden["fields"].items():
        prediksi = flat.get(field)
        if is_empty(harapan):
            if not is_empty(prediksi):
                spurious += 1
            continue
        diharapkan += 1
        if is_empty(prediksi):
            continue
        terisi += 1
        verdict, _ = compare(harapan, prediksi)
        if verdict in BENAR:
            benar += 1
        elif field in kritis:
            salah_kritis.append(f"{field}: '{prediksi}' (seharusnya '{harapan}')")

    ringkas = json.loads((HASIL / f"{doc_key}__{engine}.json").read_text())
    # Hasil run lama belum menyimpan jumlah baris tabel; dihitung ulang dari parse.md
    # supaya hasil yang sudah ada tidak perlu dijalankan ulang hanya demi satu kolom.
    baris_tabel = ringkas.get("baris_tabel")
    if baris_tabel is None:
        md = BENCH / engine / "parsing" / f"{stem}.parse.md"
        baris_tabel = (
            sum(
                1
                for ln in md.read_text(encoding="utf-8").splitlines()
                if ln.lstrip().startswith("|")
            )
            if md.exists()
            else 0
        )
    return {
        "doc": doc_key,
        "engine": engine,
        "diharapkan": diharapkan,
        "terisi": terisi,
        "benar": benar,
        "spurious": spurious,
        "salah_kritis": salah_kritis,
        "item_diharapkan": golden.get("expected_item_count"),
        "item_didapat": len((pred.get("data") or {}).get("List Item/Barang") or []),
        "detik": ringkas.get("timings", {}).get("total_s"),
        "parse_s": ringkas.get("timings", {}).get("parse_s"),
        "md_chars": ringkas.get("md_chars"),
        "baris_tabel": baris_tabel,
    }


def main() -> None:
    baris = [r for e in ENGINES for d in DOCS if (r := nilai_satu(d, e))]
    if not baris:
        print("Belum ada hasil. Jalankan dulu: python bench_ocr.py --all")
        return

    print("\n=== Per dokumen ===")
    print(
        f"{'mesin':10} {'dok':4} {'harap':>6} {'terisi':>7} {'benar':>6} "
        f"{'compl%':>7} {'acc%':>6} {'item':>7} {'tabel':>6} {'parse_s':>8} "
        f"{'total_s':>8} {'char':>7}"
    )
    for r in baris:
        c = 100 * r["terisi"] / r["diharapkan"] if r["diharapkan"] else 0
        a = 100 * r["benar"] / r["diharapkan"] if r["diharapkan"] else 0
        print(
            f"{r['engine']:10} {r['doc']:4} {r['diharapkan']:6} {r['terisi']:7} "
            f"{r['benar']:6} {c:7.1f} {a:6.1f} "
            f"{str(r['item_didapat']) + '/' + str(r['item_diharapkan']):>7} "
            f"{r['baris_tabel']:6} {r['parse_s']:8} {r['detik']:8} {r['md_chars']:7}"
        )

    print("\n=== Ringkasan per mesin (3 dokumen digabung) ===")
    print(
        f"{'mesin':10} {'harap':>6} {'terisi':>7} {'benar':>6} {'compl%':>7} {'acc%':>6} "
        f"{'spur':>5} {'parse_s':>8} {'total_s':>8}"
    )
    ringkasan = {}
    for eng in ENGINES:
        rr = [r for r in baris if r["engine"] == eng]
        if not rr:
            continue
        h = sum(r["diharapkan"] for r in rr)
        t = sum(r["terisi"] for r in rr)
        b = sum(r["benar"] for r in rr)
        s = sum(r["spurious"] for r in rr)
        ps = sum(r["parse_s"] for r in rr) / len(rr)
        ts = sum(r["detik"] for r in rr) / len(rr)
        ringkasan[eng] = {
            "dok": len(rr),
            "diharapkan": h,
            "terisi": t,
            "benar": b,
            "spurious": s,
            "completeness": round(100 * t / h, 1) if h else 0,
            "accuracy": round(100 * b / h, 1) if h else 0,
            "parse_s": round(ps, 1),
            "total_s": round(ts, 1),
            "salah_kritis": [x for r in rr for x in r["salah_kritis"]],
        }
        print(
            f"{eng:10} {h:6} {t:7} {b:6} {ringkasan[eng]['completeness']:7} "
            f"{ringkasan[eng]['accuracy']:6} {s:5} {ps:8.1f} {ts:8.1f}"
        )

    out = HASIL / "_ringkasan.json"
    out.write_text(
        json.dumps({"per_dokumen": baris, "per_mesin": ringkasan}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n💾 {out}")

    print("\n=== Kesalahan di field kritis ===")
    for eng, v in ringkasan.items():
        if v["salah_kritis"]:
            print(f"\n{eng}:")
            for x in v["salah_kritis"][:8]:
                print(f"   • {x[:150]}")
        else:
            print(f"\n{eng}: tidak ada kesalahan di field kritis")


if __name__ == "__main__":
    main()
