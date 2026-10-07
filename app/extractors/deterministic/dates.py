"""
Open ADE — Deterministic date parsing untuk teks Indonesia.
"""

import re
from datetime import date

MONTHS_ID = {
    "januari": 1,
    "jan": 1,
    "februari": 2,
    "pebruari": 2,
    "feb": 2,
    "maret": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mei": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "agustus": 8,
    "agu": 8,
    "agt": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "oktober": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "nopember": 11,
    "desember": 12,
    "des": 12,
    "dec": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "june": 6,
    "july": 7,
    "august": 8,
    "october": 10,
    "december": 12,
}

_TEXT_DATE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,10})\.?\s+(\d{4})\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b")


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[date]:
    """Semua tanggal yang terbaca di teks, urut sesuai kemunculan."""
    if not text:
        return []
    hits = []
    for m in _TEXT_DATE.finditer(text):
        month = MONTHS_ID.get(m.group(2).lower())
        if month:
            parsed = _safe_date(int(m.group(3)), month, int(m.group(1)))
            if parsed:
                hits.append((m.start(), parsed))
    for m in _ISO_DATE.finditer(text):
        parsed = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if parsed:
            hits.append((m.start(), parsed))
    for m in _NUMERIC_DATE.finditer(text):
        parsed = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if parsed:
            hits.append((m.start(), parsed))
    hits.sort(key=lambda h: h[0])
    return [d for _, d in hits]


def parse_id_date(text: str) -> date | None:
    """'2 Juni 2026' / '2026-06-02' / '02/06/2026' -> date(2026, 6, 2)."""
    dates = find_dates(text)
    return dates[0] if dates else None
