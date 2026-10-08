"""
Open ADE — Evidence matcher: skor kecocokan antara nilai hasil ekstraksi dan teks blok dokumen.

Perbaikan terhadap grounding_linker lama:
- Teks yang kebetulan mengandung satu angka (mis. alamat "Jl. ... No. 1") dulu diperlakukan
  sebagai angka murni lalu cocok ke blok mana pun yang memuat digit "1" dengan skor 0.97.
- Angka dicocokkan per token angka yang di-parse (150.000.000 == 150000000), bukan
  substring digit ("1" in "2026" dulu dianggap cocok).
- str(150000000.0) = "150000000.0" dulu menambah digit 0 sehingga nominal sering gagal cocok.
- Containment memakai batas kata, dan skor turun jika query hanya potongan kecil dari paragraf
  panjang.
"""

import re
from difflib import SequenceMatcher

from app.extractors.deterministic.numbers import numbers_in_text, parse_id_number

_NUMERIC_QUERY = re.compile(r"^(?:Rp\.?\s*)?[\d][\d.,\s]*(?:,-)?$", re.IGNORECASE)


def _tokenize(text: str) -> list[str]:
    """Tokenize teks menjadi kata-kata lowercase alfanumerik (panjang >= 2)."""
    clean = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return [w for w in clean.split() if len(w) >= 2]


def _normalize_text(text: str) -> str:
    """Lowercase + collapse whitespace."""
    if not text:
        return ""
    s = re.sub(r"[\r\n\t]+", " ", text.lower().strip())
    return re.sub(r"\s+", " ", s).strip()


def canon(text: str) -> str:
    """Bentuk kanonis untuk pencocokan: lowercase, 'JI.'->'jl.' (typo OCR), tanda baca -> spasi."""
    s = (text or "").lower()
    s = re.sub(r"\bji\.", "jl.", s)
    s = re.sub(r"[^\w/%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _token_overlap_f1(query_tokens: list[str], item_tokens: list[str]) -> float:
    if not query_tokens or not item_tokens:
        return 0.0
    query_set, item_set = set(query_tokens), set(item_tokens)
    overlap = query_set & item_set
    if not overlap:
        return 0.0
    recall = len(overlap) / len(query_set)
    precision = len(overlap) / len(item_set)
    return 2.0 * recall * precision / (recall + precision)


def _fuzzy_ratio(s1: str, s2: str) -> float:
    return SequenceMatcher(None, s1, s2).ratio()


def is_numeric_query(query: str) -> bool:
    return bool(_NUMERIC_QUERY.match((query or "").strip()))


def _number_match(query: str, item_text: str, ocr_factor: float) -> tuple[float, str | None]:
    value = parse_id_number(query)
    if value is None:
        return 0.0, None
    if not any(abs(n - value) < 0.5 for n in numbers_in_text(item_text)):
        return 0.0, None
    integer_digits = len(str(int(abs(value))))
    if integer_digits <= 2:
        # Angka kecil (volume 1, nomor 2) hanya valid jika bloknya memang hanya angka itu.
        if len(canon(item_text)) <= 6:
            return round(0.90 * ocr_factor, 3), "number_match"
        return 0.0, None
    return round(min(0.98, 0.99 * ocr_factor), 3), "number_match"


# identifier
# Nomor dokumen, nomor rekening, NPWP, part number. Bentuknya: ada digit, dan dipisah
# titik/garis miring/strip/spasi.
_IDENTIFIER_SHAPE = re.compile(r"^(?=.*\d)[\w][\w\s./\-]{4,}$")
_SEPARATOR = re.compile(r"[\s./\-_]+")

# Pasangan karakter yang rutin tertukar OCR pada teks berhuruf besar + angka.
# Dipakai HANYA untuk mencocokkan, tidak pernah untuk menulis ulang nilainya: menyamakan
# O dan 0 saat membandingkan itu toleransi; menukarnya di dalam nilai itu mengarang.
_OCR_CONFUSABLE = str.maketrans(
    {"O": "0", "o": "0", "I": "1", "l": "1", "|": "1", "S": "5", "B": "8"}
)


def looks_like_identifier(text: str) -> bool:
    """Apakah teks ini berbentuk nomor dokumen / rekening / NPWP, bukan kalimat biasa."""
    t = (text or "").strip()
    if not t or " " in t.strip() and len(t.split()) > 6:
        return False
    return bool(_IDENTIFIER_SHAPE.match(t))


def identifier_skeleton(text: str, fold_ocr: bool = False) -> str:
    """
    Buang semua pemisah, sisakan huruf+angka saja.

        "K.TEL.054321/HK.810/T1R-0D000000/2026"  -> "KTEL054321HK810T1R0D0000002026"
        "K. TEL.054321/HK.810/T1R-0D000000/2026" -> "KTEL054321HK810T1R0D0000002026"

    Dua bentuk di atas adalah nomor yang SAMA; yang kedua hanya hasil OCR menyisipkan
    spasi. Tanpa pembandingan berbasis skeleton, `canon()` memecah keduanya jadi token
    berbeda ("tel 054321" vs "tel054321") dan nomor kontrak — field paling kritis di
    dokumen pengadaan — gagal ditemukan di dokumennya sendiri.
    """
    skeleton = _SEPARATOR.sub("", (text or "").strip())
    skeleton = re.sub(r"[^\w]", "", skeleton)
    return skeleton.translate(_OCR_CONFUSABLE).upper() if fold_ocr else skeleton.upper()


def _identifier_match(query: str, item_text: str, ocr_factor: float) -> tuple[float, str | None]:
    """
    Cocokkan identifier terhadap setiap token identifier di dalam blok.

    Dua tingkat, sengaja dibedakan skornya:
      - skeleton sama persis          -> kuat; beda query & dokumen hanya pemisah
      - skeleton sama setelah melipat karakter OCR yang sering tertukar (O/0, I/1)
                                      -> lebih lemah, karena toleransinya lebih longgar
    """
    q_skel = identifier_skeleton(query)
    if len(q_skel) < 6:
        return 0.0, None

    kandidat = [t for t in re.split(r"\s{2,}|[,;()\[\]]", item_text or "") if t.strip()]
    kandidat.append(item_text or "")
    # Juga coba gabungan token bersebelahan: OCR memecah "K. TEL.054321" jadi dua token.
    kata = (item_text or "").split()
    kandidat += [
        "".join(kata[i : i + n]) for n in (2, 3, 4) for i in range(max(0, len(kata) - n + 1))
    ]

    q_folded = identifier_skeleton(query, fold_ocr=True)
    terbaik = (0.0, None)
    for c in kandidat:
        c_skel = identifier_skeleton(c)
        if not c_skel:
            continue
        if c_skel == q_skel:
            return round(min(0.98, 0.97 * ocr_factor), 3), "identifier_match"
        if identifier_skeleton(c, fold_ocr=True) == q_folded:
            skor = round(min(0.93, 0.92 * ocr_factor), 3)
            if skor > terbaik[0]:
                terbaik = (skor, "identifier_match_ocr_tolerant")
    return terbaik


def calculate_match_confidence(
    query: str,
    item_text: str,
    ocr_confidence: float = 0.95,
) -> tuple[float, str | None]:
    """
    Skor [0..0.99] seberapa kuat item_text menjadi evidence untuk query.
    Mengembalikan (0.0, None) jika tidak memenuhi ambang kualitas.
    """
    q_norm, it_norm = _normalize_text(query), _normalize_text(item_text)
    if not q_norm or not it_norm:
        return 0.0, None

    ocr_factor = max(
        0.80, min(1.0, ocr_confidence if ocr_confidence and ocr_confidence > 0 else 0.95)
    )

    if is_numeric_query(query):
        return _number_match(query, item_text, ocr_factor)

    # Identifier didahulukan: `canon()` memecahnya di setiap titik/garis miring, sehingga
    # nomor dokumen yang OCR-nya menyisipkan satu spasi tidak pernah cocok lewat jalur teks.
    if looks_like_identifier(query):
        skor, jenis = _identifier_match(query, item_text, ocr_factor)
        if jenis:
            return skor, jenis

    q_c, it_c = canon(query), canon(item_text)
    if not q_c or not it_c:
        return 0.0, None

    if q_c == it_c:
        return round(0.99 * ocr_factor, 3), "exact_match"

    # Semua skor dikali ocr_factor secara seragam, supaya exact match tidak pernah
    # kalah dari containment pada blok dengan confidence OCR yang sama.
    if len(q_c) >= 3 and f" {q_c} " in f" {it_c} ":
        coverage = len(q_c) / len(it_c)
        return round(min(0.98, (0.72 + 0.26 * coverage**0.3) * ocr_factor), 3), "phrase_containment"

    if len(it_c) >= 6 and f" {it_c} " in f" {q_c} ":
        coverage = len(it_c) / len(q_c)
        if coverage >= 0.60:
            return round(min(0.92, (0.72 + 0.18 * coverage) * ocr_factor), 3), "reverse_containment"

    q_tokens, it_tokens = _tokenize(q_c), _tokenize(it_c)
    token_f1 = _token_overlap_f1(q_tokens, it_tokens)
    if token_f1 < 0.40:
        return 0.0, None
    matcher = SequenceMatcher(None, q_c, it_c)
    if matcher.real_quick_ratio() < 0.30:
        return 0.0, None
    combined = token_f1 * 0.65 + matcher.ratio() * 0.35
    if combined >= 0.50:
        norm_val = (combined - 0.50) / 0.50
        return round(
            min(0.94, (0.72 + 0.22 * norm_val**0.8) * ocr_factor), 3
        ), "semantic_token_match"
    return 0.0, None
