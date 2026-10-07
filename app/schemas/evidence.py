"""
Open ADE — Evidence & validation schemas (Plan §14, §15, §25).
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import BoundingBox


class FieldStatus(str, Enum):
    AUTO_VERIFIED = "AUTO_VERIFIED"  # nilai ditemukan persis di dokumen + lolos semua rule
    AUTO_ACCEPTED = "AUTO_ACCEPTED"  # evidence cukup kuat, tapi tidak persis
    REVIEW_REQUIRED = "REVIEW_REQUIRED"  # evidence lemah, atau ada rule warning
    UNSUPPORTED = "UNSUPPORTED"  # nilai TIDAK ditemukan di dokumen mana pun
    CONFLICT = "CONFLICT"  # melanggar rule konsistensi (error)
    MISSING = "MISSING"  # tidak terisi


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    rule: str
    severity: Severity
    fields: list[str]
    message: str
    expected: Any | None = None
    actual: Any | None = None


class ValidationReport(BaseModel):
    status: str = Field(description="pass | warn | fail")
    rule_version: str
    checked_rules: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    def issues_for(self, field: str) -> list[ValidationIssue]:
        return [i for i in self.issues if field in i.fields]


class FieldEvidence(BaseModel):
    field: str
    value: Any
    source_document: str
    page: int | None = None
    bbox: BoundingBox | None = None
    evidence_text: str | None = None
    block_ids: list[str] = Field(default_factory=list)
    match_type: str | None = None
    evidence_score: float = 0.0
    confidence: float = 0.0
    status: FieldStatus
    issues: list[str] = Field(default_factory=list)
