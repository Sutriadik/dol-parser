"""
Open ADE — Deteksi salinan ganda di dalam satu berkas.

Kontrak "half signed" sering dipindai dua kali ke satu PDF (salinan untuk tiap pihak).
KONTRAK_HALF_SIGNED_ST_108.pdf berisi 16 halaman = kontrak 8 halaman x 2. Tanpa deteksi ini
LLM melihat setiap item dua kali: 12 baris rincian padahal 6, dan jumlah harga item tidak
lagi cocok dengan nilai kontrak.

Kemiripan dihitung dari KUMPULAN kata, bukan urutan teks: OCR salinan kedua sering
menghasilkan urutan baca berbeda. Pada ST_108 perbandingan berurutan hanya memberi 0,05-0,22
untuk beberapa pasangan salinan, sedangkan kumpulan kata memberi 0,78-0,99 -- jauh dari
halaman bertetangga yang memang berbeda (0,04-0,30).
"""

from __future__ import annotations

import re

PAGE_BREAK = "<!-- PAGE BREAK -->"
# Setiap halaman salinan harus semirip ini dengan padanannya. Nilai terendah yang teramati
# pada salinan asli 0,78; tertinggi pada halaman berbeda 0,30.
MIN_PAGE_SIMILARITY = 0.6
MIN_COPY_PAGES = 2


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))


def _similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(len(a | b), 1)


def find_duplicate_tail(pages: list[str]) -> int | None:
    """
    Indeks halaman (0-based) tempat salinan kedua dimulai, atau None.

    Sekumpulan halaman di AKHIR berkas dianggap salinan bila setiap halamannya mirip dengan
    halaman berjarak `k` sebelumnya, untuk satu `k` yang sama. Hanya pola "salinan utuh di
    belakang" yang ditangani; halaman kembar yang tersebar tidak disentuh.
    """
    words = [_words(p) for p in pages]
    n = len(pages)
    for k in range(1, n):
        tail = n - k
        if tail < MIN_COPY_PAGES or tail > k:
            continue
        if all(
            words[i] and _similarity(words[i], words[i - k]) >= MIN_PAGE_SIMILARITY
            for i in range(k, n)
        ):
            return k
    return None


def drop_duplicate_copy(markdown: str) -> tuple[str, list[int]]:
    """Markdown tanpa salinan ganda di belakang + nomor halaman (1-based) yang dibuang."""
    pages = markdown.split(PAGE_BREAK)
    start = find_duplicate_tail(pages)
    if start is None:
        return markdown, []
    kept = PAGE_BREAK.join(pages[:start]).rstrip()
    return kept, list(range(start + 1, len(pages) + 1))
