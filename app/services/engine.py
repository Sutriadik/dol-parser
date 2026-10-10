"""
Open ADE — Engine Orchestrator.

Profile → Parse (routed) → Document IR → Classify → Extract → Validate → Evidence → Output.

Setiap run menyimpan provenance (versi parser/schema/prompt/rule/model, jumlah LLM call,
durasi per tahap) supaya perubahan akurasi bisa ditelusuri sumbernya (Plan §26–27).
"""

import json
import re
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import config
from app.document_ir.adapter import content_hash, from_parsed_response
from app.document_ir.models import DocumentIR
from app.evidence.locator import build_field_evidence
from app.extractors.prompts import PROMPT_VERSION
from app.ingestion.profiler import DocumentProfile, profile_document
from app.logger import logger
from app.schemas.bast import BASTExtractionSchema
from app.schemas.common import BoundingBox, FieldVisualGrounding, LandingAIParsedResponse
from app.schemas.contract import ContractExtractionSchema
from app.schemas.evidence import FieldEvidence, FieldStatus, ValidationReport
from app.schemas.sph import SPHExtractionSchema
from app.services.classifier import DocumentClassifier
from app.services.duplicate_pages import drop_duplicate_copy
from app.validation.rules import (
    RULE_VERSION,
    check_amounts_grounded,
    note_duplicate_pages,
    validate_extraction,
)

MIN_CHARS_PER_PAGE_AFTER_OCR = 50
CONTRACT_TYPES = {"contract", "spk", "perjanjian"}
SPH_TYPES = {"sph", "penawaran", "quote"}
BAST_TYPES = {"bast"}


class ParsingError(Exception):
    """Raised when document parsing fails."""


class ExtractionError(Exception):
    """Raised when data extraction fails."""


class OpenADEEngine:
    """Parser & model dimuat lazy: server FastAPI tidak memuat torch/paddle sebelum ada request."""

    def __init__(self):
        self._docling = None
        self._paddle = None
        self._extractor = None
        self._refiner = None
        self.classifier = DocumentClassifier()

    @property
    def docling_parser(self):
        if self._docling is None:
            from app.parsers.docling_parser import DoclingParser

            self._docling = DoclingParser()
        return self._docling

    @property
    def paddle_parser(self):
        if self._paddle is None:
            from app.parsers.paddle_parser import PaddleOCRParser

            self._paddle = PaddleOCRParser()
        return self._paddle

    @property
    def extractor(self):
        if self._extractor is None:
            from app.extractors.ollama_client import OllamaExtractor

            self._extractor = OllamaExtractor()
        return self._extractor

    @property
    def refiner(self):
        if self._refiner is None:
            from app.parsers.markdown_refiner import MarkdownRefiner

            self._refiner = MarkdownRefiner()
        return self._refiner

    # stage 1: parse
    def profile(self, pdf_path: str, max_pages: int = None) -> DocumentProfile | None:
        try:
            return profile_document(pdf_path, max_pages=max_pages)
        except Exception as e:
            logger.warning(f"Profiling gagal ({e}); dokumen diperlakukan sebagai non-PDF")
            return None

    def parse(
        self,
        pdf_path: str,
        max_pages: int = None,
        profile: DocumentProfile | None = None,
        ocr: str | None = None,
    ) -> LandingAIParsedResponse:
        """
        `ocr` adalah SATU tombol pemilihan mesin OCR; None = env `OCR_ENGINE`:

            engine.parse(pdf, ocr="rapidocr")   # default, lintas platform
            engine.parse(pdf, ocr="mac")        # Apple Vision, hanya macOS
            engine.parse(pdf, ocr="tesseract")  # Tesseract CLI
            engine.parse(pdf, ocr="paddle")     # PaddleOCR (jalur parser terpisah)

        Tiga nilai pertama berjalan di atas Docling (layout + TableFormer tetap dipakai);
        "paddle" memakai jalur PP-Structure tersendiri.

        Mesin yang jalan SELALU mesin yang diminta. Dulu galat Docling apa pun -- termasuk
        nama mesin yang salah ketik -- diam-diam dialihkan ke PaddleOCR, jadi hasilnya bisa
        datang dari mesin lain tanpa ada yang tahu. Sekarang galat itu dilempar; untuk
        mencoba mesin lain, minta terang-terangan lewat `ocr=`.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise ParsingError(f"File tidak ditemukan: {pdf_path}")

        profile = profile or self.profile(pdf_path, max_pages)
        if profile is not None:
            needs_ocr = profile.needs_ocr
            logger.info(
                f"{path.name}: {profile.kind.upper()} ({profile.page_count} hlm, OCR hlm: "
                f"{profile.ocr_pages[:10] or '-'})"
            )
        else:
            needs_ocr = path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

        selected = (ocr or config.OCR_ENGINE).lower()

        started = time.time()
        if selected == "paddle":
            parsed = self.paddle_parser.parse(pdf_path, max_pages=max_pages)
        else:
            # "auto" dari pemanggil berarti "terserah server": Docling membaca OCR_ENGINE.
            docling_ocr = None if selected == "auto" else selected
            try:
                parsed = self.docling_parser.parse(
                    pdf_path, do_ocr=needs_ocr, max_pages=max_pages, ocr_engine=docling_ocr
                )
            except Exception as e:
                raise ParsingError(
                    f"Docling gagal membaca {path.name} (ocr={selected}): {e}. "
                    'Mesin lain tidak dicoba otomatis; minta lewat ocr= (mis. "paddle").'
                ) from e

        # Poles teks markdown (reparasi typo / teks rumpang OCR & format tabel LaTeX-grade)
        if config.ENABLE_LLM_MARKDOWN_REFINER:
            refined_md = self.refiner.refine_markdown(parsed.markdown)
            if refined_md and refined_md != parsed.markdown:
                parsed.markdown = refined_md
                parsed.metadata.output_markdown_chars = len(refined_md)

        logger.info(
            f"Parsing [{parsed.metadata.parser_engine}]: {parsed.metadata.page_count} hlm | "
            f"{parsed.metadata.output_markdown_chars} karakter | {time.time() - started:.1f}s"
        )
        return parsed

    @staticmethod
    def ensure_enough_text(parsed: LandingAIParsedResponse) -> None:
        """
        Jangan kirim dokumen (hampir) kosong ke LLM: model cenderung menghasilkan JSON tanpa henti
        sampai timeout, dan hasilnya pasti halusinasi. Gagal cepat dengan pesan yang jelas.
        """
        pages = max(parsed.metadata.page_count, 1)
        real_chars = len(re.sub(r"\[IMAGE[^\]]*\]|<!--.*?-->|\s+", "", parsed.markdown))
        if real_chars < MIN_CHARS_PER_PAGE_AFTER_OCR * pages:
            raise ParsingError(
                f"Teks hasil parsing terlalu sedikit ({real_chars} karakter untuk {pages} halaman, "
                f"engine {parsed.metadata.parser_engine}). Ekstraksi LLM dibatalkan. "
                f"Cek kualitas scan, atau coba mesin OCR lain lewat ocr= (mac, tesseract, paddle)."
            )

    # stage 2: extract
    def extract(
        self, markdown_text: str, doc_type: str = "auto"
    ) -> tuple[ContractExtractionSchema | SPHExtractionSchema | BASTExtractionSchema, str]:
        resolved = doc_type.lower()
        if resolved == "auto":
            classification = self.classifier.classify(markdown_text)
            resolved = classification.document_type
            logger.info(
                f"Auto-Classified: [{resolved.upper()}] ({classification.confidence * 100:.0f}%)"
            )

        self.extractor.reset_stats()
        started = time.time()
        try:
            if resolved in SPH_TYPES:
                extracted, resolved = self.extractor.extract_sph(markdown_text), "sph"
            elif resolved in BAST_TYPES:
                extracted, resolved = self.extractor.extract_bast(markdown_text), "bast"
            else:
                if resolved not in CONTRACT_TYPES:
                    logger.warning(
                        f"Belum ada schema untuk tipe '{resolved}', memakai schema kontrak"
                    )
                extracted, resolved = self.extractor.extract_contract(markdown_text), "contract"
        except Exception as e:
            raise ExtractionError(f"Extraction gagal untuk tipe '{resolved}': {e}") from e
        logger.info(
            f"Extraction selesai ({time.time() - started:.1f}s, {self.extractor.llm_calls} LLM "
            "call)"
        )
        return extracted, resolved

    # reporting
    @staticmethod
    def build_quality_report(
        evidence: list[FieldEvidence], validation: ValidationReport
    ) -> dict[str, Any]:
        total = len(evidence)
        filled = [e for e in evidence if e.status != FieldStatus.MISSING]
        grounded = [e for e in filled if e.evidence_score > 0]
        status_counts = Counter(e.status.value for e in evidence)
        report = {
            "total_fields": total,
            "filled_fields": len(filled),
            "null_fields": [e.field for e in evidence if e.status == FieldStatus.MISSING],
            "fill_rate": round(len(filled) / max(total, 1), 3),
            "grounded_fields": len(grounded),
            "grounding_rate": round(len(grounded) / max(len(filled), 1), 3),
            "status_counts": dict(status_counts),
            # UNSUPPORTED wajib ikut: ia dipecah dari REVIEW_REQUIRED, dan kalau tidak
            # didaftarkan di sini field yang nilainya TIDAK ADA di dokumen justru hilang
            # dari daftar yang harus dilihat PM -- kebalikan dari maksudnya.
            "review_required_fields": [
                e.field
                for e in evidence
                if e.status
                in (FieldStatus.REVIEW_REQUIRED, FieldStatus.UNSUPPORTED, FieldStatus.CONFLICT)
            ],
            # Didaftarkan terpisah supaya bisa ditaruh paling atas di antrean review:
            # nilai tanpa bukti di dokumen adalah kandidat karangan model yang paling kuat.
            "unsupported_fields": [
                e.field for e in evidence if e.status == FieldStatus.UNSUPPORTED
            ],
            "validation_status": validation.status,
            "validation_issue_count": len(validation.issues),
            "requires_pm_confirmation": True,
        }
        logger.info(
            f"\n{'=' * 60}\nQUALITY REPORT\n{'=' * 60}\n"
            f"  Fill rate      : {len(filled)}/{total} ({report['fill_rate']:.0%})\n"
            f"  Grounding rate : {len(grounded)}/{len(filled)} ({report['grounding_rate']:.0%})\n"
            f"  Status         : {dict(status_counts)}\n"
            f"  Tanpa bukti    : {len(report['unsupported_fields'])} field tidak ditemukan di "
            "dokumen\n"
            f"  Validation     : {validation.status} ({len(validation.issues)} "
            f"issue)\n{'=' * 60}"
        )
        for issue in validation.issues:
            logger.warning(
                f"  [{issue.severity.value}] {issue.rule}: {issue.message} "
                f"(expected={issue.expected}, actual={issue.actual})"
            )
        return report

    @staticmethod
    def evidence_to_visual_groundings(evidence: list[FieldEvidence]) -> dict[str, dict[str, Any]]:
        """Format lama `visual_groundings` tetap disediakan untuk konsumen yang sudah ada."""
        result = {}
        for e in evidence:
            if e.bbox is None or e.page is None:
                continue
            result[e.field] = FieldVisualGrounding(
                field_name=e.field,
                extracted_value=e.value,
                page=e.page,
                box=BoundingBox(**e.bbox.model_dump()),
                confidence=e.evidence_score,
                source_snippet=(e.evidence_text or "")[:200],
            ).model_dump()
        return result

    # end-to-end
    def process_full(
        self,
        pdf_path: str,
        doc_type: str = "auto",
        output_dir: str = None,
        max_pages: int = None,
        ocr: str | None = None,
    ) -> dict[str, Any]:
        """`ocr`: rapidocr | mac | tesseract | paddle | auto (lihat self.parse)."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        timings: dict[str, float] = {}
        t0 = time.time()
        path = Path(pdf_path)

        parsing_dir = Path(output_dir) / "parsing" if output_dir else config.PARSING_OUTPUT_DIR
        extraction_dir = (
            Path(output_dir) / "extraction" if output_dir else config.EXTRACTION_OUTPUT_DIR
        )
        parsing_dir.mkdir(parents=True, exist_ok=True)
        extraction_dir.mkdir(parents=True, exist_ok=True)

        t = time.time()
        profile = self.profile(pdf_path, max_pages)
        timings["profile_s"] = round(time.time() - t, 2)

        t = time.time()
        parsed = self.parse(pdf_path, max_pages=max_pages, profile=profile, ocr=ocr)
        timings["parse_s"] = round(time.time() - t, 2)
        parse_md_file = parsing_dir / f"{path.stem}.parse.md"
        parse_json_file = parsing_dir / f"{path.stem}.parse.json"
        parse_md_file.write_text(parsed.markdown, encoding="utf-8")
        # indent=2 supaya enak dibaca di editor. "markdown" dikeluarkan karena isinya sama dengan
        # *.parse.md di sebelahnya.
        parse_json_data = parsed.model_dump(exclude_none=True, exclude={"markdown"})
        parse_json_file.write_text(
            json.dumps(parse_json_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # document_id diturunkan dari ISI BERKAS, bukan dari hasil OCR-nya: ia dipakai sebagai
        # kunci upsert NocoDB, jadi memproses ulang PDF yang sama (mesin OCR lain, nama berkas
        # lain dari n8n) harus memperbarui baris yang sama -- bukan membuat baris kembar.
        ir: DocumentIR = from_parsed_response(
            parsed, file_name=path.name, document_id=content_hash(pdf_path)
        )
        self.ensure_enough_text(parsed)

        # Berkas berisi dua salinan dokumen yang sama: LLM hanya diberi salinan pertama.
        # Grounding tetap memakai semua halaman -- teks salinan kedua tetap teks asli.
        extraction_md, duplicate_pages = drop_duplicate_copy(parsed.markdown)
        if duplicate_pages:
            logger.warning(
                f"Halaman {duplicate_pages[0]}-{duplicate_pages[-1]} adalah salinan "
                f"ganda; hanya salinan pertama yang diekstrak."
            )

        t = time.time()
        extracted, detected_type = self.extract(extraction_md, doc_type=doc_type)
        timings["extract_s"] = round(time.time() - t, 2)
        ir.document_type = detected_type
        data = extracted.model_dump(by_alias=True)

        t = time.time()
        validation = validate_extraction(detected_type, data)
        evidence = build_field_evidence(data, ir, validation, min_score=config.GROUNDING_MIN_SCORE)
        validation = check_amounts_grounded(validation, detected_type, evidence)
        validation = note_duplicate_pages(validation, duplicate_pages)
        timings["validate_and_ground_s"] = round(time.time() - t, 2)
        quality_report = self.build_quality_report(evidence, validation)
        timings["total_s"] = round(time.time() - t0, 2)

        run_info = {
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "document_id": ir.document_id,
            "parser_engine": parsed.metadata.parser_engine,
            "parser_version": config.PARSER_VERSION,
            "schema_version": config.SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
            "rule_version": RULE_VERSION,
            "model": config.OLLAMA_MODEL,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "page_count": parsed.metadata.page_count,
            # Laporan mutu dari mesin pembaca; masuk NocoDB sebagai "Mutu Pembacaan".
            "parse_quality": parsed.metadata.mutu_pembacaan,
            "duplicate_pages": duplicate_pages,
            "block_count": ir.block_count,
            "llm_calls": self.extractor.llm_calls,
            "llm_seconds": round(self.extractor.llm_seconds, 1),
            "timings": timings,
        }
        payload = {
            "document_name": path.name,
            "document_type": detected_type,
            "data": data,
            "validation": validation.model_dump(mode="json"),
            "evidence": [e.model_dump(mode="json") for e in evidence],
            "visual_groundings": self.evidence_to_visual_groundings(evidence),
            "quality_report": quality_report,
            "document_profile": profile.to_dict() if profile else None,
            "run_info": run_info,
            # Ikut masuk NocoDB sebagai kolom "Isi Dokumen" (app/companion/common.py),
            # supaya PM bisa membaca dokumennya saat mengonfirmasi field per field.
            "markdown": parsed.markdown,
        }
        extract_json_file = extraction_dir / f"{path.stem}.extract.json"
        extract_json_file.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        logger.info(
            f"\nPipeline selesai dalam {timings['total_s']}s ({run_info['llm_calls']} LLM "
            "call)\n"
            f"  {parse_md_file}\n  {parse_json_file}\n  {extract_json_file}"
        )
        return {
            "parsed": parsed,
            "ir": ir,
            "extracted": extracted,
            "document_type": detected_type,
            "validation": validation,
            "evidence": evidence,
            "quality_report": quality_report,
            "run_info": run_info,
            "files": {
                "markdown": str(parse_md_file),
                "parse_json": str(parse_json_file),
                "extract_json": str(extract_json_file),
            },
        }
