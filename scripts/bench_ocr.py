#!/usr/bin/env python3
"""
Open ADE — Benchmark mesin OCR (satu dokumen, satu mesin, satu jalan).

    python bench_ocr.py --list
    python bench_ocr.py --engine rapidocr --doc kl
    python bench_ocr.py --all                    # lanjut dari yang belum selesai
    python bench_ocr.py --consistency rapidocr   # parse 2x, bandingkan hash

Sengaja bisa dilanjutkan: tiap kombinasi disimpan terpisah, dan yang sudah ada dilewati.
Satu kombinasi bisa makan 3-5 menit, jadi mengulang semuanya dari nol hanya karena satu
gagal itu pemborosan yang tidak perlu.

Keluaran mentah ada di storage/outputs/bench/<engine>/ supaya tiap mesin tidak
menimpa hasil mesin lain -- inilah sebabnya dipakai output_dir terpisah, bukan folder
storage/outputs biasa.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("OLLAMA_KEEP_ALIVE", "60m")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCS = {
    "kl": "KL FULL SIGNED.pdf",
    "spk": "SPK_ATS_ORACLE_FULL_SIGNED.pdf",
    "np": "NP_diskominfo_aceh_tamiang_2026 (1).pdf",
}
# Ketiga dokumen uji adalah scan, jadi jalur tanpa OCR tidak menghasilkan teks sama sekali.
# EasyOCR dibuang dari proyek 2026-10-07; hasil benchmark lamanya tetap terbaca lewat
# tabel_excel.py.
ENGINES = ["rapidocr", "mac", "tesseract", "paddle"]

BENCH = ROOT / "storage" / "outputs" / "bench"
HASIL = BENCH / "_hasil"


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def run_one(engine: str, doc_key: str, force: bool = False) -> dict:
    HASIL.mkdir(parents=True, exist_ok=True)
    out = HASIL / f"{doc_key}__{engine}.json"
    if out.exists() and not force:
        print(f"⏭️  {doc_key}/{engine}: sudah ada, dilewati")
        return json.loads(out.read_text())

    from app.services.engine import OpenADEEngine

    pdf = ROOT / "sample_pdfs" / DOCS[doc_key]
    print(f"▶️  {doc_key}/{engine} … ", end="", flush=True)
    t0 = time.time()
    try:
        hasil = OpenADEEngine().process_full(str(pdf), output_dir=str(BENCH / engine), ocr=engine)
    except Exception as e:
        rec = {
            "doc": doc_key,
            "engine": engine,
            "gagal": f"{type(e).__name__}: {e}",
            "wall_s": round(time.time() - t0, 1),
        }
        out.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        print(f"❌ {type(e).__name__}: {e}")
        return rec

    # process_full mengembalikan campuran dict dan objek pydantic. Dinormalkan lewat JSON
    # sekali di sini supaya sisa skrip tidak perlu tahu mana yang mana.
    hasil = json.loads(
        json.dumps(
            hasil,
            ensure_ascii=False,
            default=lambda o: o.model_dump(by_alias=True) if hasattr(o, "model_dump") else str(o),
        )
    )
    md = (BENCH / engine / "parsing" / f"{pdf.stem}.parse.md").read_text(encoding="utf-8")
    ri = hasil.get("run_info", {})
    rec = {
        "doc": doc_key,
        "engine": engine,
        "document_type": hasil.get("document_type"),
        "timings": ri.get("timings", {}),
        "wall_s": round(time.time() - t0, 1),
        "llm_calls": ri.get("llm_calls"),
        "page_count": ri.get("page_count"),
        "md_chars": len(md),
        # Jumlah karakter saja menyesatkan: Tesseract menghasilkan 32.301 karakter pada
        # KL FULL SIGNED tapi NOL baris tabel -- seluruh tabel harganya hilang. Jadi
        # struktur dihitung terpisah dari volume teks.
        "baris_tabel": sum(1 for ln in md.splitlines() if ln.lstrip().startswith("|")),
        "md_sha1": _sha1(md),
        "validation": (hasil.get("validation") or {}).get("status"),
        "fields_filled": None,  # diisi oleh skor_ocr.py
    }
    out.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8")
    # Hasil ekstraksi penuhnya TIDAK disalin ke sini: pipeline sudah menulis
    # <engine>/extraction/<nama>.extract.json, dan itu yang dibaca skor_ocr.py. Menyalin
    # nilai kembalian process_full sempat menyesatkan -- bentuknya beda (kunci "extracted"
    # berisi objek pydantic, bukan "data"), sehingga skor terbaca 0 padahal hasilnya ada.
    t = rec["timings"]
    print(
        f"✅ parse {t.get('parse_s')}s + extract {t.get('extract_s')}s "
        f"= {t.get('total_s')}s | {rec['md_chars']} char"
    )
    return rec


def consistency(engine: str, doc_key: str, n: int = 2) -> dict:
    """Parse saja, diulang. Tahap OCR yang diuji di sini, bukan LLM-nya -- kalau OCR
    sendiri sudah tidak stabil, angka apa pun sesudahnya tidak bisa dipercaya."""
    from app.services.engine import OpenADEEngine

    pdf = ROOT / "sample_pdfs" / DOCS[doc_key]
    hashes, waktu = [], []
    for i in range(n):
        e = OpenADEEngine()  # instance baru = cache converter dingin
        t0 = time.time()
        parsed = e.parse(str(pdf), ocr=engine)
        waktu.append(round(time.time() - t0, 1))
        hashes.append(_sha1(parsed.markdown))
        print(f"   run {i + 1}: {waktu[-1]}s  sha1={hashes[-1]}  {len(parsed.markdown)} char")
    sama = len(set(hashes)) == 1
    print(f"   -> {'✅ identik' if sama else '❌ BERBEDA antar-run'}")
    return {"engine": engine, "doc": doc_key, "hashes": hashes, "waktu": waktu, "identik": sama}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--doc", choices=list(DOCS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--consistency", metavar="ENGINE", choices=ENGINES)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        print("Dokumen:")
        [print(f"  {k:5} {v}") for k, v in DOCS.items()]
        print("Mesin OCR:")
        [print(f"  {e}") for e in ENGINES]
        return
    if a.consistency:
        print(json.dumps(consistency(a.consistency, a.doc or "spk"), indent=2))
        return
    if a.all:
        for eng in ENGINES:
            for d in DOCS:
                run_one(eng, d, a.force)
        return
    if a.engine and a.doc:
        run_one(a.engine, a.doc, a.force)
        return
    if a.engine:
        for d in DOCS:
            run_one(a.engine, d, a.force)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
