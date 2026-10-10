from typing import Any, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class BoundingBox(BaseModel):
    """Normalized bounding box coordinates [0.0 - 1.0]"""

    xmin: float
    ymin: float
    xmax: float
    ymax: float


def full_page_bbox() -> BoundingBox:
    """Bbox fallback saat elemen tidak punya koordinat spesifik (mencakup seluruh halaman)."""
    return BoundingBox(xmin=0.0, ymin=0.0, xmax=1.0, ymax=1.0)


class TextRange(BaseModel):
    start: int
    end: int


class Grounding(BaseModel):
    page: int
    range: TextRange
    box: BoundingBox
    confidence: float | None = Field(0.95, description="Confidence score of detection")


class AtomicGrounding(BaseModel):
    page: int
    range: TextRange
    box: BoundingBox
    text: str | None = None
    confidence: float | None = 0.95


class StructureItem(BaseModel):
    type: str = Field(
        description="paragraph, header, table, key_value, logo, signature, stamp, etc."
    )
    id: str | None = None
    text: str | None = None
    grounding: Grounding | None = None
    atomic_grounding: list[AtomicGrounding] | None = None
    children: list["StructureItem"] | None = None
    confidence: float | None = 0.95


class DocumentStructure(BaseModel):
    type: str = "document"
    children: list[StructureItem] = []


class ParseMetadata(BaseModel):
    job_id: str
    model_version: str = "open-ade-hybrid-v1"
    page_count: int
    output_markdown_chars: int
    range_units: str = "unicode_codepoints"
    duration_ms: int = 0
    is_scanned: bool = False
    parser_engine: str = "docling+paddleocr"
    # Halaman yang teksnya terbaca parser tapi tidak sampai ke markdown akhir:
    # [{"halaman": 13, "cakupan": 0.71, "contoh": ["pendampingan", ...]}]. Kosong = utuh.
    # Tidak mendeteksi baris yang sudah dibuang di dalam OCR (skor di bawah ambang).
    teks_hilang: list[dict[str, Any]] = []
    # Laporan mutu konversi dari mesin pembaca (Docling `ConversionResult.confidence`):
    # {"rata_rata": "good", "terendah": "fair", "skor_layout": .., "skor_ocr": .., "skor_teks": ..}.
    # None = mesin tidak melaporkannya (jalur PaddleOCR, atau Docling sebelum 2.34).
    mutu_pembacaan: dict[str, Any] | None = None


class LandingAIParsedResponse(BaseModel):
    """Exact 1-to-1 schema compatibility with LandingAI ADE Parse output"""

    markdown: str
    metadata: ParseMetadata
    structure: DocumentStructure


class FieldVisualGrounding(BaseModel):
    """Visual grounding reference for an extracted field (LandingAI ADE standard)"""

    field_name: str
    extracted_value: Any
    page: int
    box: BoundingBox
    confidence: float
    source_snippet: str | None = None
