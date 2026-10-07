"""
Open ADE — Document Intermediate Representation (Plan §9).

Satu format internal untuk semua perception engine. Layer extraction, evidence,
dan validation hanya membaca DocumentIR, tidak lagi bergantung pada format
output masing-masing parser.
"""

from pydantic import BaseModel, Field

BBox = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax), ternormalisasi 0..1

CANONICAL_BLOCK_TYPES = {
    "title",
    "section_header",
    "paragraph",
    "list",
    "table",
    "table_cell",
    "header",
    "footer",
    "caption",
    "image",
    "signature",
    "key_value",
    "unknown",
}


def union_bbox(boxes: list[BBox]) -> BBox | None:
    """Bounding box gabungan (xmin/ymin minimum, xmax/ymax maksimum) dari sekumpulan box."""
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


class DocumentBlock(BaseModel):
    document_id: str
    block_id: str
    page: int
    block_type: str = "unknown"
    raw_type: str | None = Field(None, description="Tipe asli dari engine sebelum dikanonisasi")
    text: str
    bbox: BBox | None = None
    reading_order: int
    source_engine: str
    source_confidence: float | None = None
    char_range: tuple[int, int] | None = Field(
        None, description="Rentang karakter pada markdown hasil parsing"
    )


class DocumentPage(BaseModel):
    page_number: int
    width: float | None = None
    height: float | None = None
    blocks: list[DocumentBlock] = Field(default_factory=list)


class DocumentIR(BaseModel):
    document_id: str
    file_name: str
    document_type: str = "unknown"
    source_engine: str
    is_scanned: bool = False
    markdown: str = ""
    pages: list[DocumentPage] = Field(default_factory=list)

    @property
    def block_count(self) -> int:
        return sum(len(p.blocks) for p in self.pages)
