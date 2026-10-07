"""
Open ADE — Aturan tunggal "tipe blok -> Markdown", dipakai SEMUA parser.

Sebelumnya aturan ini hanya ada di docling_parser.py, sedangkan jalur PaddleOCR
(layout_parser.regions_to_markdown) punya perender sendiri yang lebih tua. Akibatnya
keluaran PaddleOCR tidak punya isian formulir bertanda, footer bertanda, maupun judul
yang konsisten -- padahal permintaannya jelas: markdown harus membedakan footer, judul
section, tabel, paragraf, dan isian formulir, apa pun mesin OCR-nya. Dua perender untuk
satu format pasti lama-lama berbeda, jadi aturannya dipindah ke sini dan dipakai bersama.
"""

import re

# Deteksi judul dokumen utama / Bab (Level 1: #)
_TITLE_PATTERNS = re.compile(
    r"^(SURAT\s+(PERJANJIAN|PERINTAH|TUGAS)|PERJANJIAN\s+(KERJASAMA|KONTRAK)|"
    r"BERITA\s+ACARA|NOTA\s+PESANAN|KERANGKA\s+ACUAN|SYARAT-SYARAT|"
    r"BAB\s+[IVXLCDM0-9]+)\b",
    re.IGNORECASE,
)

# Deteksi sub-sub klausul: 1.1.1, 2.3.1 (Level 4: ####)
_SUB_SUB_HEADING = re.compile(r"^\d+\.\d+\.\d+(\.|\s+[A-Z])")

# Deteksi sub-klausul: 1.1, 1.2, 2.1 (Level 3: ###)
_SUB_HEADING = re.compile(r"^\d+\.\d+(\.|\s+[A-Z])")


def infer_heading_markdown(text: str, block_type: str = "section_header") -> str:
    """
    Menentukan kedalaman Markdown heading (#, ##, ###, ####) secara hierarkis.

    Terinspirasi dari fitur --heading-hierarchy pada OpenDataLoader PDF:
    - Dokumen Utama / BAB / Judul Halaman Utama -> # Level 1
    - Klausul/Pasal Utama (PASAL 1, 1. WAKTU, A. KETENTUAN) -> ## Level 2
    - Sub-klausul (1.1, 2.1, dsb.) -> ### Level 3
    - Sub-sub butir (1.1.1, 2.1.1, dsb.) -> #### Level 4
    """
    raw = text.strip()
    if raw.startswith("#"):
        return raw

    clean_text = raw.lstrip("#").strip()

    if block_type == "title" or _TITLE_PATTERNS.search(clean_text):
        return f"# {clean_text}"
    if _SUB_SUB_HEADING.match(clean_text):
        return f"#### {clean_text}"
    if _SUB_HEADING.match(clean_text):
        return f"### {clean_text}"
    return f"## {clean_text}"


# Penanda butir: angka "1." "1)" "(1)", satu huruf "a." "B)" "(c)", atau romawi "ii." "(IV)".
# Sengaja bukan "1-4 huruf apa saja": "Jl." / "No." / "PT." di awal alamat bukan penanda.
_ROMAWI = r"[ivxlcdm]{1,6}|[IVXLCDM]{1,6}"
_PENANDA = re.compile(
    rf"^(?:\((?P<k>[0-9]{{1,3}}|[A-Za-z]|{_ROMAWI})\)|(?P<t>[0-9]{{1,3}}|[A-Za-z]|{_ROMAWI})(?P<p>[.)]))"
    r"(?=\s|$)"
)
_NILAI_ROMAWI = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
# Indentasi 3 spasi per tingkat: cukup untuk bersarang di bawah "- " (isi mulai kolom 2)
# maupun "1. " (kolom 3) menurut CommonMark, dan belum jadi blok kode (4 spasi).
_INDENT = "   "


def _romawi(s: str) -> int | None:
    angka = [_NILAI_ROMAWI.get(c) for c in s.lower()]
    if None in angka:
        return None
    total = sum(-a if a < b else a for a, b in zip(angka, angka[1:] + [0]))
    return total if total > 0 else None


def _kandidat(line: str) -> tuple[str, list[tuple[str, int]]] | None:
    """-> (penanda, [(gaya, nilai), ...]). Gaya = jenis + bentuk, mis. "huruf-kecil.".

    Satu huruf yang juga angka romawi ("i", "v", "x", "C", ...) punya dua kandidat; yang
    benar dipilih oleh `PenataList` dari konteks butir sebelumnya.
    """
    m = _PENANDA.match(line)
    if not m:
        return None
    isi = m.group("k") or m.group("t")
    bentuk = "()" if m.group("k") else m.group("p")
    if isi.isdigit():
        return m.group(0), [(f"angka{bentuk}", int(isi))]
    besar = "besar" if isi.isupper() else "kecil"
    kandidat = []
    if len(isi) == 1:
        kandidat.append((f"huruf-{besar}{bentuk}", ord(isi.lower()) - 96))
    nilai = _romawi(isi)
    if nilai:
        kandidat.append((f"romawi-{besar}{bentuk}", nilai))
    if not kandidat:
        return None  # mis. "ab." -- bukan huruf tunggal, bukan romawi
    return m.group(0), kandidat


def pasang_penanda_list(text: str, marker: str | None) -> str:
    """Tempel penanda butir dari Docling (`item.marker`) ke depan teksnya.

    Docling memisahkan "(1)"/"a."/"II." dari isi butir. Dulu hanya `item.text` yang
    dipakai, sehingga di markdown ayat (1)-(5) dan butir a-f kehilangan nomornya --
    padahal Pasal 7 "dilampiri syarat a-f" dirujuk lewat huruf itu. Bullet simbol
    ("•", "-") tidak ditempel: perender sudah menuliskannya sebagai "- ".
    """
    marker = (marker or "").strip()
    if not marker or text.startswith(marker) or not _kandidat(marker + " "):
        return text
    return f"{marker} {text}"


class PenataList:
    """Menentukan tingkat indentasi butir list, untuk SEMUA dokumen.

    Tidak ada urutan baku "(1) -> a. -> i.": dokumen lain memakai "A. -> 1) -> a)" atau
    "I. -> A. -> 1.". Aturannya sama dengan cara orang membaca:
    - gaya penanda yang pertama muncul = tingkat 0;
    - gaya yang belum pernah muncul = satu tingkat lebih dalam dari butir sebelumnya;
    - gaya yang sudah ada di atasnya = kembali ke tingkat itu.
    Huruf ambigu diselesaikan dengan "nomor berikutnya yang diharapkan": "i." setelah
    "h." adalah huruf, "i." yang membuka daftar baru adalah romawi; "C." hasil OCR di
    antara "b." dan "d." tetap huruf kecil.

    Satu objek dipakai sepanjang dokumen (daftar bisa menyeberang halaman) dan di-reset
    di setiap judul. Butir tanpa penanda ditaruh di tingkat 0 tanpa mengubah tumpukan:
    tingkatnya tidak bisa diketahui dari teks saja.
    """

    def __init__(self) -> None:
        self._tumpukan: list[list] = []  # [[gaya, nilai_terakhir], ...] tingkat 0 di depan

    def reset(self) -> None:
        self._tumpukan = []

    def tingkat(self, line: str) -> int | None:
        hasil = _kandidat(line)
        if hasil is None:
            return None
        penanda, kandidat = hasil
        pembuka_romawi = penanda.strip("().").lower() == "i"
        tumpukan = self._tumpukan
        gaya_kasus_lain = [
            (g.replace("besar", "kecil") if "besar" in g else g.replace("kecil", "besar"), n)
            for g, n in kandidat
            if g.startswith("huruf")
        ]

        def ambil(i: int, gaya: str, nilai: int) -> int:
            del tumpukan[i + 1 :]
            tumpukan[i] = [gaya, nilai]
            return i

        # 1. Lanjutan berurutan dari tingkat mana pun (terdalam dulu), termasuk salah kapital.
        for i in range(len(tumpukan) - 1, -1, -1):
            gaya_ada, terakhir = tumpukan[i]
            for gaya, nilai in kandidat + gaya_kasus_lain:
                if gaya == gaya_ada and nilai == terakhir + 1:
                    return ambil(i, gaya_ada, nilai)
        # 2. Gaya yang sama walau nomornya melompat (OCR membuang satu butir). Kecuali "i."
        #    yang tidak melanjutkan "h.": itu pembuka daftar romawi, bukan huruf yang melompat.
        for i in range(len(tumpukan) - 1, -1, -1):
            for gaya, nilai in kandidat:
                if gaya == tumpukan[i][0] and not (pembuka_romawi and gaya.startswith("huruf")):
                    return ambil(i, gaya, nilai)
        # 3. Gaya baru -> satu tingkat lebih dalam. Huruf yang juga romawi: "i"/"I" pembuka
        #    daftar lazimnya romawi, selain itu huruf.
        gaya, nilai = kandidat[-1] if pembuka_romawi else kandidat[0]
        tumpukan.append([gaya, nilai])
        return len(tumpukan) - 1

    def baris(self, line: str) -> str:
        if line.startswith(("- ", "* ")):
            return line
        tingkat = self.tingkat(line)
        indent = _INDENT * (tingkat or 0)
        if tingkat is not None and re.match(r"^[0-9]{1,3}[.)]\s", line):
            # "- 1. teks" di CommonMark jadi list bernomor di dalam bullet; tulis langsung.
            return f"{indent}{line}"
        return f"{indent}- {line}"


def block_to_markdown(block_type: str, text: str, penata: PenataList | None = None) -> str:
    """Render satu blok yang tipenya sudah ditentukan.

    `penata` dibawa sepanjang dokumen supaya tingkat list tetap benar antar-blok dan
    antar-halaman; tanpa itu setiap blok dianggap daftar baru.
    """
    if block_type in ("heading", "section_header", "title"):
        if penata:
            penata.reset()
        return infer_heading_markdown(text, block_type=block_type)
    if block_type == "list_item":
        # Satu blok bisa berisi beberapa butir yang digabung block_grouper (satu per baris);
        # tiap baris jadi butirnya sendiri, bukan hanya baris pertama.
        penata = penata or PenataList()
        return "\n".join(penata.baris(ln.strip()) for ln in text.splitlines() if ln.strip())
    if block_type == "key_value":
        # Isian formulir: "Nama : Sutriadi Kurniawan" -> "- **Nama** : Sutriadi Kurniawan"
        label, sep, value = text.partition(":")
        if sep and label.strip() and len(label) < 80:
            return f"- **{label.strip()}** : {value.strip()}"
        return f"- {text}"
    if block_type == "caption":
        return f"_{text}_"
    if block_type == "footer":
        return f"<!-- FOOTER: {text} -->"
    if block_type in ("logo", "image"):
        return (
            f"<!-- {block_type.upper()}: {text} -->" if text else f"<!-- {block_type.upper()} -->"
        )
    return text  # paragraph & table sudah dalam bentuk final
