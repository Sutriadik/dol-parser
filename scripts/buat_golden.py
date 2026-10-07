#!/usr/bin/env python3
"""
Open ADE — bikin rangka berkas golden dari hasil ekstraksi.

    python scripts/buat_golden.py "SPH Vendor X.pdf"
    python scripts/buat_golden.py "SPH Vendor X.pdf" --timpa

Kenapa ada: menambah dokumen golden sebelumnya berarti mengetik ulang puluhan nama field
dengan tangan, dan satu salah ketik membuat field itu dihitung "missing" selamanya tanpa
ada yang menyadarinya. Skrip ini membaca `storage/outputs/extraction/<nama>.extract.json`
yang sudah ada, lalu menuliskan rangka golden dengan nama field yang PASTI cocok --
tinggal dikoreksi NILAINYA.

Alur pemakaian:

    1. .venv311/bin/python run.py "SPH Vendor X.pdf"
    2. .venv311/bin/python scripts/buat_golden.py "SPH Vendor X.pdf"
    3. buka eval/golden/SPH Vendor X.json, BANDINGKAN dengan PDF aslinya,
       perbaiki setiap nilai yang salah
    4. .venv311/bin/python -m eval.run_eval

Langkah 3 tidak bisa dilewati. Nilai yang ditulis skrip ini adalah tebakan mesin; kalau
dipakai apa adanya sebagai golden, evaluasinya cuma membandingkan mesin dengan dirinya
sendiri dan selalu 100% -- angka yang terlihat bagus dan tidak berarti apa-apa.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import config  # noqa: E402

# Field yang salahnya paling mahal per jenis dokumen. Dipakai metrik `critical_accuracy`:
# salah baca nomor kontrak atau harga berujung tagihan ditolak, sementara salah baca
# "Keterangan" tidak. Daftar ini hanya usulan awal -- sesuaikan per dokumen.
CRITICAL_DEFAULT = {
    "contract": [
        "Nomor Kontrak Kerja",
        "Nama Pekerjaan",
        "Pihak Pertama.Nama Perusahaan",
        "Pihak Kedua.Nama Perusahaan",
        "sub total",
        "Total PPN",
        "Total Harga Pekerjaan",
    ],
    "sph": [
        "Nomor SPH",
        "Tanggal SPH",
        "Vendor.Nama Vendor",
        "Subtotal",
        "Grand Total",
    ],
    "bast": [
        "Nomor BAST",
        "Nama Pekerjaan",
        "Tanggal Serah Terima",
        "Pihak Penyerah.Nama Perusahaan",
        "Pihak Penerima.Nama Perusahaan",
    ],
}


def ratakan(data: Any, prefix: str = "") -> dict[str, Any]:
    """
    Nested dict/list -> {"Pihak Pertama.Nama Perusahaan": ..., "List Item[0].volume": ...}

    Bentuk kunci sengaja disamakan persis dengan yang dipakai `eval/run_eval.py`; kalau
    keduanya berbeda satu karakter saja, seluruh field terbaca "missing".
    """
    rata: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            jalur = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                rata.update(ratakan(value, jalur))
            else:
                rata[jalur] = value
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            jalur = f"{prefix}[{idx}]"
            if isinstance(value, (dict, list)):
                rata.update(ratakan(value, jalur))
            else:
                rata[jalur] = value
    return rata


def main() -> int:
    ap = argparse.ArgumentParser(description="Bikin rangka golden dari hasil ekstraksi.")
    ap.add_argument("dokumen", help='Nama berkas PDF, mis. "SPH Vendor X.pdf"')
    ap.add_argument(
        "--timpa",
        action="store_true",
        help="Timpa berkas golden yang sudah ada (HATI-HATI: koreksi manual hilang)",
    )
    ap.add_argument("--keluar", default=None, help="Folder tujuan (default: eval/golden/)")
    args = ap.parse_args()

    stem = Path(args.dokumen).stem
    sumber = config.EXTRACTION_OUTPUT_DIR / f"{stem}.extract.json"
    if not sumber.exists():
        print(
            f"✗ Belum ada hasil ekstraksi: {sumber}\n"
            f'  Jalankan dulu:  .venv311/bin/python run.py "{args.dokumen}"',
            file=sys.stderr,
        )
        return 1

    hasil = json.loads(sumber.read_text(encoding="utf-8"))
    doc_type = hasil.get("document_type", "unknown")
    fields = ratakan(hasil.get("data") or {})

    # Item BoQ dihitung supaya eval bisa menandai baris tabel yang hilang atau berlebih --
    # kesalahan yang tidak terlihat kalau hanya nilai per field yang dibandingkan.
    daftar_item = next(
        (
            v
            for k, v in (hasil.get("data") or {}).items()
            if isinstance(v, list) and k.lower().startswith(("list item", "daftar"))
        ),
        [],
    )

    tujuan_dir = Path(args.keluar) if args.keluar else ROOT / "eval" / "golden"
    tujuan_dir.mkdir(parents=True, exist_ok=True)
    tujuan = tujuan_dir / f"{stem}.json"
    if tujuan.exists() and not args.timpa:
        print(
            f"✗ Sudah ada: {tujuan}\n"
            f"  Berkas ini mungkin memuat koreksi manual. Pakai --timpa kalau memang mau diganti.",
            file=sys.stderr,
        )
        return 1

    golden = {
        "document": Path(args.dokumen).name,
        "doc_type": doc_type,
        "profile": (hasil.get("document_profile") or {}).get("profile_name", ""),
        "source": f"RANGKA OTOMATIS dari {sumber.name} — NILAINYA BELUM DIKOREKSI",
        "notes": (
            "PERIKSA SETIAP NILAI TERHADAP PDF ASLINYA sebelum dipakai. Field yang tidak "
            "ada di dokumen ditulis null, bukan string kosong. Hapus baris field yang "
            "memang tidak relevan untuk dokumen ini."
        ),
        "belum_dikoreksi": True,  # eval/run_eval.py memperingatkan selama flag ini True
        "critical_fields": CRITICAL_DEFAULT.get(doc_type, []),
        "fields": fields,
        "expected_item_count": len(daftar_item),
    }
    tujuan.write_text(json.dumps(golden, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"✓ Rangka golden ditulis: {tujuan}")
    print(f"  tipe dokumen    : {doc_type}")
    print(
        "  field terisi    : "
        f"{sum(1 for v in fields.values() if v not in (None, ''))}/{len(fields)}"
    )
    print(f"  baris item BoQ  : {len(daftar_item)}")
    print()
    print("  LANGKAH BERIKUTNYA — jangan dilewati:")
    print(f"  1. Buka {tujuan} bersebelahan dengan PDF aslinya.")
    print("  2. Perbaiki setiap nilai yang salah; tulis null untuk yang tidak ada di dokumen.")
    print('  3. Hapus baris "belum_dikoreksi" setelah selesai.')
    print("  4. .venv311/bin/python -m eval.run_eval")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
