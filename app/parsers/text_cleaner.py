"""
Open ADE — OCR Post-Processing & Text Cleaning Module

Membersihkan output OCR (PaddleOCR/Docling) sebelum dikirim ke LLM dan sebelum disimpan ke
*.parse.md.
Menghasilkan Markdown yang 100% rapi, bebas typo karakter, tanpa kata menempel, dan tabel
beralignment presisi.

Pipeline cleaning:
  1. fix_common_ocr_typos()         — Perbaiki typo karakter OCR (TELK0M, 0racle, Isiam, dll.)
  2. fix_missing_spaces()           — Perbaiki kata/angka/tanda baca yang menempel
  3. fix_entity_and_legal_spacing() — Perbaiki kata kapital, nomor pasal, lampiran, badan usaha
  4. normalize_entity_casing()      — Normalisasi casing entitas resmi
  5. clean_signature_noise()        — Hapus artefak noise stempel/tanda tangan (MmEto, fer, dll.)
  6. clean_footer_noise()           — Bersihkan footer/running noise yang menyusup ke teks
  7. fix_number_formatting()        — Normalisasi format angka dan rupiah
  8. normalize_whitespace()         — Collapse spasi berlebih, rapikan newline
  9. reformat_tables()              — Format ulang tabel ke standar LaTeX tabular
  10. clean_ocr_text()              — Master function yang mengorkestrasi seluruh pipeline
"""

import re

from app.parsers.table_converter import reformat_markdown_tables_in_text

# ============================================================================
# 1. Common OCR Typo Dictionary
# ============================================================================

OCR_TYPO_MAP: dict[str, str] = {
    # Angka ↔ Huruf confusion
    "Nom0r": "Nomor",
    "nom0r": "nomor",
    "N0mor": "Nomor",
    "n0mor": "nomor",
    "0racle": "Oracle",
    "0racie": "Oracle",
    "Oracie": "Oracle",
    "oracie": "oracle",
    "TELK0M": "TELKOM",
    "Telk0m": "Telkom",
    "TELkoM": "TELKOM",
    "TELKOMREGIONAL": "TELKOM REGIONAL",
    "REGIONALI": "REGIONAL I",
    "PlHAK": "PIHAK",
    "PiHAK": "PIHAK",
    "Plhak": "Pihak",
    "UNIvERsITAs": "UNIVERSITAS",
    "TELkOM": "TELKOM",
    "BHAKTi": "BHAKTI",
    "TEKNOvASI": "TEKNOVASI",
    "Isiam": "Islam",
    "isiam": "islam",
    "BuT": "BUT",
    "ATs": "ATS",
    "&OPERATION": "& OPERATION",
    "&operation": "& operation",
    "Acess Point": "Access Point",
    "acess point": "access point",
    "Standart": "Standard",
    "standart": "standard",
    "Progam": "Program",
    "Procesor": "Processor",
    # Kata-kata umum Indonesia yang sering typo
    "sebesar Rp": "sebesar Rp.",
    "Yangdalam": "Yang dalam",
    "diatas": "di atas",
    "dibawah": "di bawah",
    "sebaik-baiknya.Berdasarkan": "sebaik-baiknya. Berdasarkan",
    "danSustainability": "dan Sustainability",
    "perbuatanhukum": "perbuatan hukum",
    "yangberwenang": "yang berwenang",
    # Unit dan singkatan
    "pkt": "pkt",
    "LampiranII": "Lampiran II",
    "LampiranI": "Lampiran I",
}


# Huruf kecil yang bentuknya sama dengan kapitalnya (c k o s v w x z) dan terselip di tengah
# kata kapital hampir pasti salah baca OCR ("KERJAsAMA"). Aturan ini menggantikan entri kamus
# per kata -- kamus lama sempat memuat nama penandatangan asli hanya untuk satu huruf "w".
_KAPITAL_SALAH_BACA = re.compile(r"\b([A-Z]{2,})([cksovwxz])(?=[A-Z]{2,}\b)")


def fix_common_ocr_typos(text: str) -> str:
    """Mengganti typo OCR umum berdasarkan dictionary lookup."""
    for typo, correct in OCR_TYPO_MAP.items():
        text = text.replace(typo, correct)
    return _KAPITAL_SALAH_BACA.sub(lambda m: m.group(1) + m.group(2).upper(), text)


# ============================================================================
# 2. Fix Missing Spaces & Concatenations
# ============================================================================


def fix_missing_spaces(text: str) -> str:
    """
    Memperbaiki kata/angka/simbol yang menempel tanpa spasi.
    Menangani kasus uppercase glued words, missing space after punctuation, dll.
    """
    # 1. Angka menempel huruf: "29Mei" → "29 Mei", "11Kedaung" → "11 Kedaung", "12Lebak" → "12
    # Lebak"
    text = re.sub(r"(\d)([A-Z][a-z])", r"\1 \2", text)

    # 2. Nomor pasal / lampiran / tahun / tanggal yang menempel ke angka:
    # "Nomor2" → "Nomor 2", "Tahun2025" → "Tahun 2025", "tanggal3" → "tanggal 3", "Pasal2" → "Pasal
    # 2"
    text = re.sub(
        r"\b(Nomor|Tahun|tanggal|Pasal|No\.|Lampiran)(\d+)", r"\1 \2", text, flags=re.IGNORECASE
    )

    # 3. Nomor urut list menempel di awal kata: "1TELKOM" → "1. TELKOM", "1TELKOM"
    text = re.sub(r"(?m)^(\d+)([A-Z]{2,})", r"\1. \2", text)

    # 4. Spasi setelah No. <angka>: "No.1" → "No. 1", "No.12" → "No. 12"
    text = re.sub(r"\bNo\.(\d+)", r"No. \1", text)

    # 5. Spasi antara angka desimal/kolektif dan kata: "456.000sebuah" → "456.000 sebuah"
    text = re.sub(r"(\d{3}\.\d{3})([a-zA-Z])", r"\1 \2", text)

    # 6. Kata menempel kata (CamelCase): "InformasiUniversitas" → "Informasi Universitas".
    # Guard kata kiri minimal 5 huruf supaya nama produk CamelCase (FortiGate, PowerPoint) tidak
    # terpecah
    text = re.sub(r"(?<![A-Za-z])([A-Z]?[a-z]{5,})([A-Z][a-z]{2,})", r"\1 \2", text)

    # 7. "sebesarRp" → "sebesar Rp", "Rp.174" → "Rp. 174"
    text = re.sub(r"([a-z])(Rp[\.\s])", r"\1 \2", text)
    text = re.sub(r"(Rp\.?)(\d)", r"\1 \2", text)

    # 8. Titik menempel huruf besar (awal kalimat baru): "terlampir.Berdasarkan" → "terlampir.
    # Berdasarkan"
    text = re.sub(r"\.([A-Z])", r". \1", text)

    # 9. Koma menempel huruf/kata: "Bandung,40257" → "Bandung, 40257", "2024,yang" → "2024, yang",
    # "2025,selanjutnya" → "2025, selanjutnya"
    # Koma setelah digit diikuti digit TIDAK disentuh: "166.500.000,00", "1,5" tetap utuh
    text = re.sub(r"(?<!\d),(?=[^\s,])", ", ", text)
    text = re.sub(r"(\d{4}),([A-Za-z])", r"\1, \2", text)
    text = re.sub(r"(\d),([A-Za-z])", r"\1, \2", text)

    # 10. Typo OCR "JI." (huruf I) untuk singkatan "Jl." (Jalan)
    text = re.sub(r"\bJI\.(?=\s*[A-Z0-9])", "Jl.", text)

    # 11. Titik dua menempel huruf/angka: "Alamat:Jl." → "Alamat: Jl.", "Nomor:687" → "Nomor: 687"
    text = re.sub(r":([A-Za-z])", r": \1", text)
    text = re.sub(r":(\d)", r": \1", text)

    # 12. Kurung tutup menempel huruf: ")PIHAK" → ") PIHAK", "(UIN)Kota" → "(UIN) Kota"
    text = re.sub(r"\)([A-Za-z])", r") \1", text)

    # 13. Huruf menempel kurung buka: "sah(" → "sah (", "Negeri(UIN)" → "Negeri (UIN)"
    text = re.sub(r"([a-zA-Z])\(", r"\1 (", text)

    # 14. "disebutBUT"" → 'disebut "BUT"'
    text = re.sub(r'disebut\s*"?([A-Z]{2,})"?', r'disebut "\1"', text)
    text = re.sub(r'"Para Pihak dan', r'"Para Pihak" dan', text)

    return text


# ============================================================================
# 3. Fix Entity & Legal Spacing (Uppercase Compounds)
# ============================================================================

UPPERCASE_GLUED_PAIRS = [
    (r"\bTELEKOMUNIKASIINDONESIA\b", "TELEKOMUNIKASI INDONESIA"),
    (r"\bPENYEDIAANFIREWALL\b", "PENYEDIAAN FIREWALL"),
    (r"\bCPEUNTUK\b", "CPE UNTUK"),
    (r"\bPIHAKPERTAMA\b", "PIHAK PERTAMA"),
    (r"\bPIHAKKEDUA\b", "PIHAK KEDUA"),
    (r"\bPIHAKKESATU\b", "PIHAK KESATU"),
    (r"\bPERTAMAmemberi\b", "PERTAMA memberi"),
    (r"\bKONTRAKLAYANAN\b", "KONTRAK LAYANAN"),
    (r"\bLINGKUPPEKERJAAN\b", "LINGKUP PEKERJAAN"),
    (r"\bSYARAT-SYARATPELAKSANAAN\b", "SYARAT-SYARAT PELAKSANAAN"),
    (r"\bUNIVERSITASISLAMNEGERI\b", "UNIVERSITAS ISLAM NEGERI"),
    (r"\bSURATPERINTAHKERJA\b", "SURAT PERINTAH KERJA"),
    (r"\bBERITAACARA\b", "BERITA ACARA"),
    (r"\bIslamNegeri\b", "Islam Negeri"),
    (r"\bPutihKec\.\b", "Putih Kec."),
    (r"\bAgamSumatera\b", "Agam Sumatera"),
    (r"\bKotaBandung\b", "Kota Bandung"),
    (r"\bJawaBarat\b", "Jawa Barat"),
    (r"\bCoblongKota\b", "Coblong Kota"),
    (r"\bSiliwangi-Coblong\b", "Siliwangi - Coblong"),
]


def fix_entity_and_legal_spacing(text: str) -> str:
    """Memperbaiki kata-kata entitas hukum dan kapital yang menempel dari OCR."""
    for pattern, replacement in UPPERCASE_GLUED_PAIRS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


# ============================================================================
# 4. Normalize Entity Casing
# ============================================================================

UPPERCASE_ENTITIES = [
    r"universitas\s+telkom",
    r"pt\.\s*bhakti\s+unggul\s+teknovasi",
    r"pihak\s+pertama",
    r"pihak\s+kedua",
    r"pihak\s+kesatu",
    r"surat\s+perintah\s+kerja",
    r"berita\s+acara\s+serah\s+terima",
]


def normalize_entity_casing(text: str) -> str:
    """Normalisasi casing acak dari OCR pada entitas penting."""
    for pattern in UPPERCASE_ENTITIES:
        text = re.sub(pattern, lambda m: m.group(0).upper(), text, flags=re.IGNORECASE)
    return text


# ============================================================================
# 5. Clean Signature Noise & Artifacts
# ============================================================================


def clean_signature_noise(text: str) -> str:
    """
    Menghapus artefak noise hasil OCR stempel basah / tanda tangan
    (seperti 'MmEto', 'fer', 'XMohamad', karakter sampah 1-3 huruf terisolasi di area ttd).
    """
    lines = text.split("\n")
    cleaned_lines = []

    # Pola kata sampah yang sering muncul dari stempel/tanda tangan
    noise_standalone_words = {
        "mmeto",
        "fer",
        "ttd",
        "materai",
        "ttd.",
        "cap",
        "stempel",
        "xmohamad",
        "xindah",
        "x",
        "xx",
        "xxx",
        "v",
        "vv",
    }

    for line in lines:
        stripped = line.strip()
        stripped_lower = stripped.lower()

        # Hapus baris sampah pendek 1-6 huruf yang cocok dengan noise stempel
        if stripped_lower in noise_standalone_words:
            continue

        # Hapus baris karakter acak tanpa vokal yang bermakna atau hanya simbol/huruf aneh
        if (
            len(stripped) <= 4
            and re.match(r"^[a-zA-Z\W_]+$", stripped)
            and not any(v in stripped_lower for v in "aeiou")
        ):
            continue

        # Hilangkan awalan 'X' atau 'x' pada nama penandatangan (akibat tanda tangan/silang)
        # Misal: "XBudi Santoso Wijaya" → "Budi Santoso Wijaya"
        line_clean = re.sub(r"\b[xX]([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", r"\1", line)
        cleaned_lines.append(line_clean)

    return "\n".join(cleaned_lines)


# ============================================================================
# 6. Clean Footer Noise
# ============================================================================

FOOTER_NOISE_PATTERNS = [
    # Alamat kampus panjang yang bukan konten utama
    r"Main\s+Campus\s+Bangkit\s+Building.*?(?:www\.\S+|\.ac\.id)",
    # URL standalone
    r"^www\.\S+\.\S+$",
    # Nomor telepon panjang standalone
    r"^[t\s:]*[\+]?\d[\d\s\-/\(\)]{15,}$",
]


def clean_footer_noise(text: str) -> str:
    """Menghapus noise footer yang ikut ter-parse tapi bukan konten dokumen sebenarnya."""
    for pattern in FOOTER_NOISE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return text


# ============================================================================
# 7. Fix Number Formatting
# ============================================================================


def fix_number_formatting(text: str) -> str:
    """
    Normalisasi format angka rupiah agar konsisten.
    '166.500.000-' → '166.500.000' (hilangkan dash trailing)
    """
    text = re.sub(r"(\d{1,3}(?:\.\d{3})+)\s*[-–—](?!\d)", r"\1", text)
    return text


# ============================================================================
# 8. Normalize Whitespace
# ============================================================================


_INDENT_LIST = re.compile(r"^( +)(?:[-*] |\d{1,3}[.)] )")


def normalize_whitespace(text: str) -> str:
    """Membersihkan whitespace berlebih tanpa menghilangkan struktur markdown."""
    lines = []
    for line in text.split("\n"):
        # Indentasi butir list bersarang ("  - a.") adalah struktur, bukan noise OCR.
        indent = _INDENT_LIST.match(line)
        rapi = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(indent.group(1) + rapi if indent else rapi)
    text = "\n".join(lines)

    # Collapse 3+ newlines berturut-turut menjadi 2
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def strip_invisible_and_control_chars(text: str) -> str:
    """
    Membersihkan karakter tak kasat mata (zero-width unicode, soft hyphen, BOM)
    dan karakter kontrol ASCII berbahaya (terinspirasi dari AI safety filter OpenDataLoader)
    yang rawan merusak tokenisasi LLM atau menyelipkan prompt injection tersembunyi.
    """
    if not text:
        return text
    # Hapus Zero-Width Space (\u200B), ZWNJ (\u200C), ZWJ (\u200D), BOM (\uFEFF), Word Joiner
    # (\u2060), Soft Hyphen (\u00AD)
    text = re.sub(r"[\u200B-\u200D\uFEFF\u2060\u00AD]", "", text)
    # Hapus karakter kontrol ASCII non-printable (kecuali \n, \t, \r)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return text


# ============================================================================
# Master Cleaning Function
# ============================================================================


def clean_ocr_text(text: str) -> str:
    """
    Master function yang menjalankan seluruh pipeline pembersihan OCR & Formatting Markdown.

    Args:
        text: Raw markdown text dari OCR engine

    Returns:
        Cleaned markdown text siap untuk LLM extraction & human viewing
    """
    if not text:
        return text

    # Step 0: AI Safety & Invisible text filter (OpenDataLoader inspired)
    text = strip_invisible_and_control_chars(text)

    # Step 1: Fix typo karakter OCR
    text = fix_common_ocr_typos(text)

    # Step 2: Fix missing spaces & concatenations
    text = fix_missing_spaces(text)

    # Step 3: Fix uppercase legal entities & compound words
    text = fix_entity_and_legal_spacing(text)

    # Step 4: Normalisasi casing entitas
    text = normalize_entity_casing(text)

    # Step 5: Bersihkan noise stempel / tanda tangan
    text = clean_signature_noise(text)

    # Step 6: Bersihkan footer noise
    text = clean_footer_noise(text)

    # Step 7: Fix format angka
    text = fix_number_formatting(text)

    # Step 8: Normalize whitespace
    text = normalize_whitespace(text)

    # Step 9: Reformat seluruh tabel Markdown menjadi standar LaTeX-grade
    text = reformat_markdown_tables_in_text(text)

    return text


def clean_ocr_line(line_text: str) -> str:
    """
    Versi ringan untuk membersihkan satu baris OCR (per-line cleaning).
    Digunakan di PaddleOCR parser sebelum merge.
    """
    if not line_text:
        return line_text

    line_text = strip_invisible_and_control_chars(line_text)
    line_text = fix_common_ocr_typos(line_text)
    line_text = fix_missing_spaces(line_text)
    line_text = fix_entity_and_legal_spacing(line_text)
    line_text = re.sub(r"\s+", " ", line_text).strip()

    return line_text
