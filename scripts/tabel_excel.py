#!/usr/bin/env python3
"""
Open ADE — Cetak hasil benchmark persis sebagai baris lembar Excel
"Perbandingan Metode OCR - Ekstraksi Kontrak".

Dibuat supaya pengisian lembar itu tidak perlu menyalin angka satu-satu dari beberapa
keluaran yang berbeda -- menyalin manual persis tempat angka jadi tertukar.

    python tabel_excel.py           # rapi untuk dibaca
    python tabel_excel.py --tsv     # untuk ditempel langsung ke Excel
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from scripts.bench_ocr import HASIL  # noqa: E402
except ImportError:
    from bench_ocr import HASIL  # noqa: E402

# Nama di Excel <- nama teknis di repo.
NAMA = {
    # easyocr: sudah dibuang dari proyek, tapi hasil benchmark lama di _hasil/ masih memuatnya.
    "easyocr": "Docling bawaan (EasyOCR)",
    "paddle": "PaddleOCR",
    "tesseract": "Tesseract",
    "mac": "Apple Vision",
    "rapidocr": "RapidOCR",
}
KOLOM = [
    "Metode OCR",
    "Kontrak Diuji (jml)",
    "Total Field Diharapkan",
    "Field Terisi",
    "Field Terisi Benar",
    "Completeness Rate (%)",
    "Accuracy Rate (%)",
    "Waktu Proses Rata-rata (detik)",
    "Konsisten Antar-run?",
    "Tahan Variasi Layout?",
    "Estimasi Biaya per Dokumen (Rp)",
    "Catatan",
]

# Diisi dari hasil pengukuran terpisah (bench_ocr.py --consistency) dan pengamatan.
# Disimpan di berkas supaya tidak tercampur dengan angka yang dihitung otomatis.
TAMBAHAN = ROOT / "storage" / "outputs" / "bench" / "_hasil" / "_catatan.json"

WATT, TARIF = 22, 1444.70  # asumsi beban MacBook Air M4; tarif PLN R-1/TR Rp/kWh


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", action="store_true")
    a = ap.parse_args()

    ring = json.loads((HASIL / "_ringkasan.json").read_text())
    tambahan = json.loads(TAMBAHAN.read_text()) if TAMBAHAN.exists() else {}

    baris = []
    for eng, v in ring["per_mesin"].items():
        t = tambahan.get(eng, {})
        biaya = WATT * v["total_s"] / 3600 / 1000 * TARIF
        baris.append(
            [
                NAMA.get(eng, eng),
                v["dok"],
                v["diharapkan"],
                v["terisi"],
                v["benar"],
                f"{v['completeness']:.1f}",
                f"{v['accuracy']:.1f}",
                f"{v['total_s']:.0f}",
                t.get("konsisten", "-"),
                t.get("layout", "-"),
                f"{biaya:.0f} (listrik; tanpa API berbayar)",
                t.get("catatan", "-"),
            ]
        )
    baris.sort(key=lambda r: float(r[6]), reverse=True)

    if a.tsv:
        print("\t".join(KOLOM))
        for r in baris:
            print("\t".join(str(x) for x in r))
        return

    for r in baris:
        print(f"\n### {r[0]}")
        for k, v in zip(KOLOM[1:], r[1:]):
            print(f"   {k:32} : {v}")


if __name__ == "__main__":
    main()
