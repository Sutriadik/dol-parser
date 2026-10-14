"""
Open ADE: Evidence locator (Plan §5.1, §14, §17, §25).

Mencari evidence setiap field di DocumentIR, lalu menggabungkannya dengan hasil
validasi menjadi FieldEvidence berstatus (AUTO_VERIFIED / REVIEW_REQUIRED / ...).

Catatan kebijakan: status AUTO_* hanya rekomendasi. Sesuai aturan proyek, klausul
kontrak dan harga tetap wajib dikunci PM per field sebelum dipakai.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.document_ir.models import BBox, DocumentIR, union_bbox
from app.evidence.matcher import calculate_match_confidence, canon, is_numeric_query
from app.extractors.deterministic.numbers import format_number_for_matching
from app.logger import logger
from app.schemas.common import BoundingBox
from app.schemas.evidence import FieldEvidence, FieldStatus, Severity, ValidationReport

# Field yang tidak bermakna untuk dicari evidence-nya (nomor urut, representasi tabel mentah).
SKIP_KEYS = {
    "Daftar Tabel Terstruktur",
    "Atribut Tambahan",
    "raw_markdown",
    "_kategori_terdeteksi",
    "Nomor Item",
    "No",
    # Diturunkan dari judul kolom OTC/MRC; mencari teks "MRC" di dokumen tidak membuktikan
    # apa pun dan hanya menambah baris "perlu dicek" di antrean PM.
    "Jenis Biaya",
}
# "identifier_match" masuk daftar kuat: skeleton nomor yang sama persis (beda hanya di
# pemisah) adalah bukti yang setara dengan containment. "identifier_match_ocr_tolerant"
# SENGAJA tidak masuk -- ia menyamakan O dengan 0 dan I dengan 1, toleransi yang cukup
# untuk menautkan bukti tapi tidak cukup untuk menyatakan nilainya terverifikasi.
STRONG_MATCH_TYPES = {"exact_match", "number_match", "phrase_containment", "identifier_match"}
MULTI_BLOCK_WINDOW = 3
MULTI_BLOCK_PENALTY = 0.03  # gabungan blok hanya menang jika jelas lebih baik dari satu blok
VERIFIED_MIN_SCORE = 0.85
MIN_SCORE_DEFAULT = 0.65

# Bobot sementara, wajib dikalibrasi ulang dengan golden dataset (Plan §17).
W_EVIDENCE, W_SOURCE, W_RULES = 0.60, 0.15, 0.25


@dataclass
class EvidenceMatch:
    score: float
    match_type: str
    page: int
    bbox: BBox | None
    text: str
    block_ids: list[str] = field(default_factory=list)
    source_confidence: float = 0.95


def _to_bounding_box(b: BBox | None) -> BoundingBox | None:
    if b is None:
        return None
    x1, x2 = (round(max(0.0, min(1.0, float(v))), 5) for v in (b[0], b[2]))
    y1, y2 = (round(max(0.0, min(1.0, float(v))), 5) for v in (b[1], b[3]))
    return BoundingBox(xmin=min(x1, x2), ymin=min(y1, y2), xmax=max(x1, x2), ymax=max(y1, y2))


def _is_better(candidate: EvidenceMatch, best: EvidenceMatch | None) -> bool:
    if best is None:
        return True
    if candidate.score > best.score + 0.005:
        return True
    return abs(candidate.score - best.score) <= 0.005 and len(candidate.text) < len(best.text)


def locate_value(
    value: Any, ir: DocumentIR, min_score: float = MIN_SCORE_DEFAULT
) -> EvidenceMatch | None:
    query = format_number_for_matching(value).strip()
    if len(query) < 2 and not query.isdigit():
        return None

    best: EvidenceMatch | None = None
    query_tokens = set(canon(query).split())

    for page in ir.pages:
        blocks = page.blocks
        for block in blocks:
            conf = block.source_confidence or 0.95
            score, match_type = calculate_match_confidence(query, block.text, ocr_confidence=conf)
            if score >= min_score and match_type:
                candidate = EvidenceMatch(
                    score, match_type, block.page, block.bbox, block.text, [block.block_id], conf
                )
                if _is_better(candidate, best):
                    best = candidate

    # Nilai panjang yang terpotong ke beberapa baris (mis. alamat 2 baris): coba gabungan blok
    # berurutan.
    if (
        (best is None or best.score < VERIFIED_MIN_SCORE)
        and not is_numeric_query(query)
        and len(query) >= 15
    ):
        for page in ir.pages:
            blocks = page.blocks
            for start in range(len(blocks)):
                if not query_tokens & set(canon(blocks[start].text).split()):
                    continue
                for size in range(2, MULTI_BLOCK_WINDOW + 1):
                    window = blocks[start : start + size]
                    if len(window) < size:
                        break
                    joined = " ".join(b.text for b in window)
                    conf = min((b.source_confidence or 0.95) for b in window)
                    score, match_type = calculate_match_confidence(
                        query, joined, ocr_confidence=conf
                    )
                    if score >= min_score and match_type:
                        candidate = EvidenceMatch(
                            round(score - MULTI_BLOCK_PENALTY, 3),
                            f"{match_type}+multiblock",
                            page.page_number,
                            union_bbox([b.bbox for b in window if b.bbox]),
                            joined,
                            [b.block_id for b in window],
                            conf,
                        )
                        if _is_better(candidate, best):
                            best = candidate
    return best


def iter_leaf_fields(data: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(data, dict):
        for key, value in data.items():
            if key in SKIP_KEYS:
                continue
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)):
                yield from iter_leaf_fields(value, path)
            else:
                yield path, value
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            yield from iter_leaf_fields(value, f"{prefix}[{idx}]")
    else:
        yield prefix, data


def _is_empty(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().lower() in ("", "null", "none", "n/a", "-")
    )


def build_field_evidence(
    extracted: dict[str, Any],
    ir: DocumentIR,
    validation: ValidationReport | None = None,
    min_score: float = MIN_SCORE_DEFAULT,
) -> list[FieldEvidence]:
    results: list[FieldEvidence] = []
    for path, value in iter_leaf_fields(extracted):
        issues = validation.issues_for(path) if validation else []
        issue_names = [i.rule for i in issues]
        has_error = any(i.severity == Severity.ERROR for i in issues)
        has_warning = any(i.severity == Severity.WARNING for i in issues)

        if _is_empty(value) or isinstance(value, bool):
            results.append(
                FieldEvidence(
                    field=path,
                    value=value,
                    source_document=ir.file_name,
                    status=FieldStatus.MISSING if _is_empty(value) else FieldStatus.REVIEW_REQUIRED,
                    issues=issue_names,
                )
            )
            continue

        match = locate_value(value, ir, min_score=min_score)
        rule_score = 0.0 if has_error else 0.5 if has_warning else 1.0
        evidence_score = match.score if match else 0.0
        source_quality = match.source_confidence if match else 0.0
        confidence = round(
            W_EVIDENCE * evidence_score + W_SOURCE * source_quality + W_RULES * rule_score, 3
        )

        if has_error:
            status = FieldStatus.CONFLICT
        elif match is None:
            # Terisi tapi tidak ditemukan di blok mana pun: hampir selalu karangan model. Dipisah
            # dari REVIEW_REQUIRED supaya antrean PM bisa diurutkan berdasar risiko.
            status = FieldStatus.UNSUPPORTED
        elif has_warning:
            status = FieldStatus.REVIEW_REQUIRED
        elif match.match_type in STRONG_MATCH_TYPES and match.score >= VERIFIED_MIN_SCORE:
            status = FieldStatus.AUTO_VERIFIED
        elif match.score >= 0.80:
            status = FieldStatus.AUTO_ACCEPTED
        else:
            status = FieldStatus.REVIEW_REQUIRED

        results.append(
            FieldEvidence(
                field=path,
                value=value,
                source_document=ir.file_name,
                page=match.page if match else None,
                bbox=_to_bounding_box(match.bbox) if match and match.bbox else None,
                evidence_text=match.text[:300] if match else None,
                block_ids=match.block_ids if match else [],
                match_type=match.match_type if match else None,
                evidence_score=evidence_score,
                confidence=confidence,
                status=status,
                issues=issue_names,
            )
        )
    return results


# Bacaan kedua untuk identifier di halaman scan. Grounding mencocokkan nilai ke teks OCR yang
# sama dengan yang dibaca LLM, jadi salah baca OCR selalu "terbukti" oleh dirinya sendiri.
# Kasus nyata: nomor kontrak Nota Pesanan terbaca tanpa satu titik dan tetap berstatus
# bukti_kuat. Tesseract membaca ulang potongan gambarnya; ini verifikasi, bukan mesin cadangan,
# jadi nilai tidak pernah diganti, hanya statusnya yang turun.
#
# Nominal sengaja tidak ikut: pada uji kontrak scan 14 halaman, 4 dari 10 harga satuan yang
# benar ikut ditandai karena Tesseract membaca tabel bergaris dengan buruk. Nominal sudah dijaga
# aturan aritmetika dan terbilang.
SECOND_OCR_FIELD_KEYS = ("Nomor", "NPWP", "Rekening")
SECOND_OCR_SKIP_KEYS = ("Nomor Item", "Nomor Pasal", "Nomor Urut")
SECOND_OCR_MAX_CONFIDENCE = 0.6  # di bawah ambang bukti_cukup, supaya naik di antrean PM
SECOND_OCR_LANG = "ind+eng"
SECOND_OCR_DPI = 300
SECOND_OCR_PAD = 0.01  # tambahan tepi potongan, pecahan lebar/tinggi halaman


def _tesseract_tersedia() -> bool:
    import shutil

    return shutil.which("tesseract") is not None


def _baca_potongan(pdf_path: str, page: int, bbox: BoundingBox) -> str:
    import subprocess
    import tempfile

    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        halaman = doc[page - 1]
        w, h = halaman.rect.width, halaman.rect.height
        clip = (
            pymupdf.Rect(
                (bbox.xmin - SECOND_OCR_PAD) * w,
                (bbox.ymin - SECOND_OCR_PAD) * h,
                (bbox.xmax + SECOND_OCR_PAD) * w,
                (bbox.ymax + SECOND_OCR_PAD) * h,
            )
            & halaman.rect
        )
        pix = halaman.get_pixmap(dpi=SECOND_OCR_DPI, clip=clip, colorspace=pymupdf.csGRAY)
    with tempfile.NamedTemporaryFile(suffix=".png") as gambar:
        pix.save(gambar.name)
        hasil = subprocess.run(
            ["tesseract", gambar.name, "stdout", "-l", SECOND_OCR_LANG, "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    return hasil.stdout


def _perlu_bacaan_kedua(ev: FieldEvidence, ocr_pages: set[int]) -> bool:
    return (
        ev.status in (FieldStatus.AUTO_VERIFIED, FieldStatus.AUTO_ACCEPTED)
        and ev.page in ocr_pages
        and ev.bbox is not None
        and isinstance(ev.value, str)
        and any(k in ev.field for k in SECOND_OCR_FIELD_KEYS)
        and not any(k in ev.field for k in SECOND_OCR_SKIP_KEYS)
    )


def _tanpa_spasi(teks: str) -> str:
    return "".join(str(teks).split()).upper()


def _baris_terdekat(bacaan: str, value: str) -> str:
    """Baris bacaan Tesseract yang paling mirip nilai, untuk ditunjukkan ke PM."""
    from difflib import SequenceMatcher

    baris = [b.strip() for b in bacaan.splitlines() if b.strip()]
    if not baris:
        return ""
    return max(
        baris, key=lambda b: SequenceMatcher(None, _tanpa_spasi(b), _tanpa_spasi(value)).ratio()
    )


def verify_identifiers_second_ocr(
    evidence: list[FieldEvidence], pdf_path: str, ocr_pages: Iterable[int]
) -> tuple[list[FieldEvidence], list[dict[str, Any]], dict[str, Any]]:
    """
    Baca ulang identifier berstatus bukti_kuat/bukti_cukup di halaman scan dengan Tesseract.

    Kembali: (evidence yang sama dengan status diperbarui, daftar yang berbeda, ringkasan untuk
    run_info). Tanpa biner tesseract tidak ada yang berubah dan ringkasan menyebutnya.
    """
    halaman_scan = set(ocr_pages)
    calon = [ev for ev in evidence if _perlu_bacaan_kedua(ev, halaman_scan)]
    if not calon:
        return evidence, [], {"mesin": "tesseract", "diperiksa": 0, "berbeda": 0}
    if not _tesseract_tersedia():
        return evidence, [], {"mesin": None, "diperiksa": 0, "alasan": "tesseract tidak terpasang"}

    berbeda: list[dict[str, Any]] = []
    galat = 0
    for ev in calon:
        try:
            bacaan = _baca_potongan(pdf_path, ev.page, ev.bbox)
        except Exception as e:  # Tesseract macet/timeout tidak boleh menggagalkan dokumen
            galat += 1
            logger.warning(f"Bacaan kedua {ev.field} dilewati: {e}")
            continue
        if _tanpa_spasi(ev.value) in _tanpa_spasi(bacaan):
            continue
        ev.status = FieldStatus.REVIEW_REQUIRED
        ev.confidence = min(ev.confidence, SECOND_OCR_MAX_CONFIDENCE)
        ev.issues = [*ev.issues, "ocr_kedua_berbeda"]
        berbeda.append(
            {
                "field": ev.field,
                "value": ev.value,
                "page": ev.page,
                "bacaan": _baris_terdekat(bacaan, ev.value)[:160],
            }
        )
    ringkas = {"mesin": "tesseract", "diperiksa": len(calon) - galat, "berbeda": len(berbeda)}
    if galat:
        ringkas["galat"] = galat
    return evidence, berbeda, ringkas
