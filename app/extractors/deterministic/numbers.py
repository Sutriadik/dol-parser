"""
Open ADE — Deterministic number handling (single source of truth).

Sebelumnya logika parsing angka tersebar di 4 tempat dengan perilaku berbeda
(context_analyzer, table_extractor, grounding_linker, text_cleaner). Semua
parsing/normalisasi angka Indonesia sekarang lewat modul ini.

Konvensi Indonesia: titik = pemisah ribuan, koma = desimal.
"""

import re
from typing import Any

_CURRENCY_PREFIX = re.compile(r"^\s*(?:Rp\.?|IDR)\s*", re.IGNORECASE)
_TRAILING_SEN = re.compile(r",\s*(?:-+|0{2})\s*$")
_ID_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_EN_THOUSANDS = re.compile(r"^\d{1,3}(?:,\d{3})+$")
_NUMBER_TOKEN = re.compile(r"\d[\d.,]*\d|\d")

# Angka format ribuan Indonesia di dalam teks, minimal 2 grup ribuan (>= 1.000.000),
# dengan guard supaya NPWP (01.234.567.8-901.000), nomor rekening, dan nomor
# dokumen tidak ikut dirusak.
_ID_MONEY_IN_TEXT = re.compile(
    r"(?<![\d.,/\-])(\d{1,3}(?:\.\d{3}){2,})(?:,(?:00|-))?(?![\d]|[.,/\-]\d)"
)


def parse_id_number(value: Any) -> float | None:
    """
    Parse angka/mata uang (format Indonesia maupun Inggris) ke float.
    Mengembalikan None jika tidak ada angka yang bisa dibaca.

    "Rp. 166.500.000,-" -> 166500000.0
    "1,5"               -> 1.5
    "12.500"            -> 12500.0   (default Indonesia: titik = ribuan)
    "1,234,567.89"      -> 1234567.89
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = _CURRENCY_PREFIX.sub("", str(value)).strip()
    s = _TRAILING_SEN.sub("", s)
    s = re.sub(r"[\s ]", "", s)
    s = re.sub(r"[^\d.,\-]", "", s)
    negative = s.startswith("-")
    s = s.strip("-")
    if not re.search(r"\d", s):
        return None

    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            s = s.replace(",", "")  # 1,234.56
    elif has_dot:
        if _ID_THOUSANDS.match(s) or s.count(".") > 1:
            s = s.replace(".", "")
    elif has_comma:
        if _EN_THOUSANDS.match(s) and s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")

    try:
        number = float(s)
    except ValueError:
        return None
    return -number if negative else number


def parse_indonesian_number(value: Any) -> float:
    """Backward-compatible wrapper: 0.0 jika tidak terbaca."""
    parsed = parse_id_number(value)
    return parsed if parsed is not None else 0.0


def numbers_in_text(text: str) -> list[float]:
    """Semua token angka di dalam teks, sudah di-parse."""
    found = []
    for token in _NUMBER_TOKEN.findall(text or ""):
        parsed = parse_id_number(token)
        if parsed is not None:
            found.append(parsed)
    return found


def normalize_id_money_in_text(text: str) -> str:
    """'Rp 166.500.000,-' -> 'Rp 166500000' tanpa merusak NPWP/nomor rekening."""
    return _ID_MONEY_IN_TEXT.sub(lambda m: m.group(1).replace(".", ""), text)


def format_number_for_matching(value: Any) -> str:
    """150000000.0 -> '150000000' (str(float) menambah '.0' yang merusak pencocokan digit)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def amounts_equal(a: float | None, b: float | None, tolerance: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerance


# ============================================================================
# Terbilang (angka dalam kata) — dipakai validator untuk cek konsistensi total
# ============================================================================

_UNIT_WORDS = {
    "nol": 0,
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
}
_SCALE_WORDS = {
    "ribu": 10**3,
    "juta": 10**6,
    "miliar": 10**9,
    "milyar": 10**9,
    "triliun": 10**12,
}
_SE_PREFIXED = {
    "sepuluh": ["satu", "puluh"],
    "sebelas": ["satu", "belas"],
    "seratus": ["satu", "ratus"],
    "seribu": ["satu", "ribu"],
    "sejuta": ["satu", "juta"],
}


def terbilang_to_number(text: str) -> int | None:
    """
    'Seratus Enam Puluh Enam Juta Lima Ratus Ribu Rupiah' -> 166500000
    Mengembalikan None jika tidak ada kata bilangan.
    """
    if not text:
        return None
    raw_tokens = re.findall(r"[a-z]+", text.lower())
    tokens: list[str] = []
    for tok in raw_tokens:
        tokens.extend(_SE_PREFIXED.get(tok, [tok]))

    total, group, digit = 0, 0, 0
    seen_number_word = False
    for tok in tokens:
        if tok in _UNIT_WORDS:
            digit += _UNIT_WORDS[tok]
            seen_number_word = True
        elif tok == "belas":
            group += digit + 10
            digit = 0
        elif tok == "puluh":
            group += digit * 10
            digit = 0
        elif tok == "ratus":
            group += digit * 100
            digit = 0
        elif tok in _SCALE_WORDS:
            group += digit
            total += (group or 1) * _SCALE_WORDS[tok]
            group, digit = 0, 0
            seen_number_word = True
        elif tok in ("koma", "sen"):
            break
    if not seen_number_word:
        return None
    return total + group + digit


# "Rp 738.150.000,- (Tujuh Ratus Tiga Puluh Delapan Juta Seratus Lima Puluh Ribu Rupiah)"
_AMOUNT_WITH_WORDS = re.compile(
    r"Rp[\s.:]*(\d[\d.,]*\d)\s*,?-*\s*\(([^()]{5,250}?)\)", re.IGNORECASE
)


def confirmed_amounts(text: str) -> list[float]:
    """
    Nominal yang ditulis dua kali di dokumen -- sebagai angka DAN terbilangnya -- dan
    keduanya sama. Urutan kemunculan, tanpa duplikat.

    Dua penulisan yang saling cocok hampir tidak mungkin sama-sama salah baca OCR, jadi
    nominal ini bisa dipercaya lebih dari tebakan LLM.
    """
    found: list[float] = []
    for number, words in _AMOUNT_WITH_WORDS.findall(text or ""):
        amount = parse_id_number(number)
        spelled = terbilang_to_number(words)
        if amount and spelled and amounts_equal(amount, float(spelled)) and amount not in found:
            found.append(amount)
    return found
