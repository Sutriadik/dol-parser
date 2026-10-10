#!/usr/bin/env python3
"""
Open ADE — runner dokumen tunggal.

    .venv311/bin/python run.py "path/ke/dokumen.pdf"

Seluruh setup ada di blok SETUP di bawah, jadi tidak perlu lagi menulis variabel
environment di terminal. Untuk sekali jalan, env dari shell tetap menang
(os.environ.setdefault), misalnya memakai PaddleOCR untuk satu dokumen:

    OCR_ENGINE=paddle .venv311/bin/python run.py "dokumen.pdf"
"""

import os
import sys
import time
from pathlib import Path

# SETUP
# WAJIB dijalankan sebelum `app.*` di-import: AppConfig membaca environment
# satu kali saat modul di-import.
SETUP = {
    # SATU tombol pemilihan mesin OCR. Default rapidocr supaya hasil di laptop sama dengan
    # hasil di server Linux/Windows -- Apple Vision ("mac") lebih cepat tapi hanya ada di
    # macOS, jadi tidak layak jadi patokan pengukuran. Mesin lain hanya jalan bila diminta
    # di sini atau lewat env; tidak ada pengalihan otomatis antar-mesin.
    "OCR_ENGINE": "rapidocr",  # rapidocr | mac | tesseract | paddle | auto
    "OLLAMA_MODEL": "qwen2.5:7b",
    "OLLAMA_NUM_CTX": "16384",  # samakan di seluruh pipeline, jangan diubah per tahap
    "OLLAMA_KEEP_ALIVE": "30m",  # model tetap di memori antar panggilan
    "OLLAMA_NUM_PREDICT": "4096",
    "ENABLE_LLM_MARKDOWN_REFINER": "1",
    "LOG_LEVEL": "INFO",
}
for _key, _value in SETUP.items():
    os.environ.setdefault(_key, _value)
# END SETUP

from app.services.engine import OpenADEEngine  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(f'Pemakaian: {Path(sys.argv[0]).name} "path/ke/dokumen.pdf"')
        sys.exit(2)

    path = Path(sys.argv[1]).expanduser()
    if not path.exists():
        print(f"❌ File tidak ditemukan: {path}")
        sys.exit(1)

    print(f"\n🚀 {path.name}")
    print(
        f"   ocr={os.environ['OCR_ENGINE']}  model={os.environ['OLLAMA_MODEL']}  "
        f"num_ctx={os.environ['OLLAMA_NUM_CTX']}\n"
    )

    started = time.time()
    result = OpenADEEngine().process_full(str(path))
    elapsed = time.time() - started

    run_info = result.get("run_info", {})
    timings = run_info.get("timings", {})

    print("\n" + "=" * 58)
    print(f"🎉 SELESAI DALAM {elapsed:.1f} DETIK")
    print("=" * 58)
    for stage in ("profile_s", "parse_s", "extract_s", "validate_and_ground_s"):
        if stage in timings:
            label = stage[:-2].replace("_", " ")
            print(f"   {label:<22} {timings[stage]:>8.1f}s")
    if run_info.get("llm_calls") is not None:
        print(
            f"   {'llm calls':<22} {run_info['llm_calls']:>8}  "
            f"({run_info.get('llm_seconds', 0):.1f}s)"
        )
    print("-" * 58)
    print(f"📄 Tipe        : {result['document_type'].upper()}")
    print(f"📑 Markdown    : {result['files']['markdown']}")
    print(f"📊 Parse JSON  : {result['files']['parse_json']}")
    print(f"💎 Extract JSON: {result['files']['extract_json']}")
    nocodb = result["files"].get("nocodb_json")
    if nocodb:
        print(f"🗄  NocoDB JSON : {nocodb}")
    print("=" * 58 + "\n")


if __name__ == "__main__":
    main()
