"""
Open ADE — Evidence locator (Plan §5.1, §14, §17, §25).

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

# Bobot sementara — wajib dikalibrasi ulang dengan golden dataset (Plan §17).
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
            # Nilai terisi tapi tidak ketemu di satu blok pun. Dulu ini dilebur ke
            # REVIEW_REQUIRED bersama "bukti lemah" dan "ada rule warning", padahal
            # penyebabnya berbeda jauh: nilai yang tidak ada di dokumen hampir selalu
            # karangan model, dan itu justru yang paling perlu dilihat PM lebih dulu.
            # Memisahkannya membuat antrean review bisa diurutkan berdasar risiko.
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
