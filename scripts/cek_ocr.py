#!/usr/bin/env python3
"""
Open ADE — Periksa mesin OCR mana yang benar-benar bisa dipakai di lingkungan ini.

Dibuat karena kegagalan OCR lintas platform hampir selalu diam: di macOS terpilih Apple
Vision, di Linux paket itu tidak ada, exception-nya tertelan, dan Docling diam-diam
memakai engine lain. Hasil di laptop dan di server jadi berbeda tanpa ada yang tahu.
Perintah ini membuat perbedaan itu kelihatan dalam 3 detik, sebelum satu dokumen pun
diproses.

    python cek_ocr.py
"""

import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _ada_modul(nama: str) -> bool:
    try:
        return importlib.util.find_spec(nama) is not None
    except (ImportError, ValueError):
        return False


def _versi(nama: str) -> str:
    try:
        from importlib.metadata import version

        return version(nama)
    except Exception:
        return "?"


def cek_tesseract() -> tuple[bool, str]:
    exe = shutil.which("tesseract")
    if not exe:
        return False, "biner 'tesseract' tidak ada di PATH"
    try:
        v = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        ).stdout.splitlines()[0]
        langs = subprocess.run(
            [exe, "--list-langs"], capture_output=True, text=True, timeout=10
        ).stdout.split()
        kurang = [bahasa for bahasa in ("ind", "eng") if bahasa not in langs]
        if kurang:
            return False, f"{v}, tapi bahasa {'+'.join(kurang)} belum dipasang"
        return True, f"{v}, bahasa ind+eng siap"
    except Exception as e:
        return False, f"gagal dijalankan: {e}"


def cek_paddle() -> tuple[bool, str]:
    if not _ada_modul("paddle"):
        return False, "paddlepaddle belum terpasang"
    try:
        import paddle

        pakai_gpu = paddle.device.is_compiled_with_cuda()
        jumlah = paddle.device.cuda.device_count() if pakai_gpu else 0
        if pakai_gpu and jumlah:
            nama = paddle.device.cuda.get_device_name(0)
            return True, f"v{_versi('paddlepaddle-gpu')} CUDA aktif — {jumlah}x {nama}"
        if pakai_gpu:
            return True, "wheel GPU terpasang TAPI tidak ada GPU terdeteksi — jatuh ke CPU"
        return True, f"v{_versi('paddlepaddle')} CPU saja (wheel non-GPU)"
    except Exception as e:
        return False, f"gagal diperiksa: {e}"


def main() -> int:
    print(f"Platform : {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python   : {sys.version.split()[0]}\n")

    hasil = {
        "rapidocr  (default)": (
            _ada_modul("rapidocr_onnxruntime"),
            f"v{_versi('rapidocr-onnxruntime')}"
            if _ada_modul("rapidocr_onnxruntime")
            else "rapidocr-onnxruntime belum terpasang",
        ),
        "tesseract": cek_tesseract(),
        "paddle": cek_paddle(),
        "mac       (Apple Vision)": (
            sys.platform == "darwin" and _ada_modul("ocrmac"),
            "siap"
            if sys.platform == "darwin" and _ada_modul("ocrmac")
            else f"hanya tersedia di macOS (platform ini: {sys.platform})",
        ),
    }
    for nama, (ok, ket) in hasil.items():
        print(f"  {'✅' if ok else '❌'} {nama:28} {ket}")

    siap = [n.split()[0] for n, (ok, _) in hasil.items() if ok]
    print(f"\nBisa dipakai: {', '.join(siap) or '(tidak ada)'}")
    if not siap:
        print("Tidak ada mesin OCR yang hidup — dokumen hasil scan tidak akan terbaca sama sekali.")
        return 1
    print("\nLangkah berikutnya:")
    print("  python bench_ocr.py --consistency paddle --doc spk   # parse saja, tanpa LLM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
