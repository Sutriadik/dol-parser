"""
Regression tests untuk bug yang ditemukan saat audit (2026-09) + layer baru
(Document IR, evidence, validation, profiler, evaluasi).
"""

import importlib.util
import re
from pathlib import Path

import pytest

from app.document_ir.adapter import from_parsed_response
from app.evidence.locator import build_field_evidence, locate_value
from app.evidence.matcher import calculate_match_confidence
from app.extractors.deterministic.dates import find_dates, parse_id_date
from app.extractors.deterministic.numbers import (
    normalize_id_money_in_text,
    parse_id_number,
    terbilang_to_number,
)
from app.parsers.table_extractor import detect_item_columns, extract_items_from_markdown_tables
from app.parsers.text_cleaner import clean_ocr_text
from app.schemas.evidence import FieldStatus
from app.services.classifier import DocumentClassifier
from app.services.context_analyzer import ContextAnalyzer
from app.validation.rules import validate_contract, validate_sph

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- numbers & dates
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Rp. 166.500.000,-", 166500000.0),
        ("166.500.000,00", 166500000.0),
        ("1,5", 1.5),
        ("12.500", 12500.0),
        ("1,234,567.89", 1234567.89),
        ("150000000.0", 150000000.0),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_id_number(raw, expected):
    assert parse_id_number(raw) == expected


def test_money_normalization_keeps_npwp_and_account_numbers():
    text = "NPWP: 01.234.567.8-901.000 rek 123.00.4567890.1 total Rp 166.500.000,- harga 900.000"
    out = normalize_id_money_in_text(text)
    assert "01.234.567.8-901.000" in out
    assert "123.00.4567890.1" in out
    assert "Rp 166500000" in out
    assert "900.000" in out


def test_terbilang_detects_dropped_words():
    assert terbilang_to_number("Seratus Enam Puluh Enam Juta Lima Ratus Ribu Rupiah") == 166500000
    assert terbilang_to_number("Seratus Enam Puluh Enam Juta Lima Ratus Rupiah") == 166000500
    assert terbilang_to_number("satu miliar dua ratus sebelas juta seribu") == 1_211_001_000
    assert terbilang_to_number("tanpa angka") is None


def test_dates():
    assert parse_id_date("2 Juni 2026").isoformat() == "2026-06-02"
    assert parse_id_date("2026-06-02").isoformat() == "2026-06-02"
    assert len(find_dates("29 Mei 2026 – 29 Mei 2027")) == 2


# ---------------------------------------------------------------- text cleaner
def test_cleaner_does_not_break_numbers_or_brands():
    out = clean_ocr_text(
        "Harga Rp 166.500.000,00 volume 1,5 FortiGate McAfee PowerEdge InformasiUniversitas JI. "
        "Radio"
    )
    assert "166.500.000,00" in out
    assert "1,5" in out
    assert "FortiGate" in out and "McAfee" in out and "PowerEdge" in out
    assert "Informasi Universitas" in out
    assert "Jl. Radio" in out


# ---------------------------------------------------------------- context analyzer
def test_entity_hints_ignore_address_numbers_and_bare_pt():
    md = (
        "Alamat : Jl. Contoh Raya No. 1, Bandung\n"
        "Akta pendirian tanggal 24 September 1991\n"
        "Pembayaran ke rekening Bank Mandiri Cabang KK STT Telkom No. 123.00.4567890.1 a.n PT. "
        "BHAKTI UNGGUL TEKNOVASI.\n"
        "Dibuat di : Bandung\nTanggal : 2 Juni 2026\n"
    )
    hints = ContextAnalyzer().get_entity_hints(md)
    assert hints["Nomor Rekening Bank"] == "123.00.4567890.1"
    assert hints["Nama Bank"] == "Bank Mandiri"
    assert hints["Nama Rekening Bank"] == "PT. BHAKTI UNGGUL TEKNOVASI"
    assert hints["Lokasi"] == "Bandung"
    assert hints["Tanggal Pembuatan Dokumen"] == "2 Juni 2026"


def test_entity_hints_skip_lowercase_bank_and_notary_phrase():
    md = (
        "melalui transfer bank ke BANK MANDIRI, Rekening Nomor: 123-00-4567890-1 atas nama PT "
        "BHAKTI UNGGUL TEKNOVASI, dimana\nAkta dibuat di hadapan Notaris"
    )
    hints = ContextAnalyzer().get_entity_hints(md)
    assert hints["Nama Bank"] == "Bank MANDIRI"
    assert "Lokasi" not in hints


def test_labeled_party_blocks():
    md = (
        "maka kami yang bertanda tangan dibawah ini :\nNama : Rina Kartika\nJabatan : Kepala "
        "Divisi\n"
        "Alamat : Gedung Arunika Lt. 3\nJl. Merpati No. 18, Semarang\n"
        "Yang dalam hal ini mewakili secara sah : PT SAMUDRA, selanjutnya disebut sebagai PIHAK "
        "PERTAMA, kepada :\n"
        "Nama : Bayu\nJabatan : Direktur\nAlamat : Jl. Kenanga No. 7, Surakarta\n"
        "Yang dalam hal ini mewakili secara sah : CV. DATA selanjutnya disebut sebagai PIHAK KEDUA"
    )
    parties = ContextAnalyzer.extract_parties_from_preamble(md)
    assert parties["pihak_pertama"]["nama_perusahaan"] == "PT SAMUDRA"
    assert parties["pihak_pertama"]["nama_representative"] == "Rina Kartika"
    assert "Jl. Merpati" in parties["pihak_pertama"]["alamat"]
    assert parties["pihak_kedua"]["alamat"] == "Jl. Kenanga No. 7, Surakarta"


# ---------------------------------------------------------------- table extractor
def test_table_columns_do_not_confuse_price_with_unit_or_qty():
    cols = detect_item_columns(
        ["No.", "Uraian Barang/Pekerjaan", "Vol", "Sat", "Harga Satuan (Rp)", "Jumlah Harga (Rp)"]
    )
    assert cols["qty"] == "Vol"
    assert cols["unit"] == "Sat"
    assert cols["price"] == ["Harga Satuan (Rp)"]
    assert cols["total"] == ["Jumlah Harga (Rp)"]

    md = (
        "| No. | Uraian Barang/Pekerjaan | Vol | Sat | Harga Satuan (Rp) | Jumlah Harga (Rp) |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Oracle Database Standard Edition 2 | 1 | pkt | 150.000.000 | 150.000.000 |\n"
    )
    items = extract_items_from_markdown_tables(md, doc_type="contract")
    assert items[0]["volume"] == 1.0
    assert items[0]["unit"] == "pkt"
    assert items[0]["Harga Satuan"] == 150000000.0


def test_tables_without_price_columns_are_not_items():
    md = "| No | Nama | Jabatan |\n|---|---|---|\n| 1 | Rina | Direktur |\n"
    assert extract_items_from_markdown_tables(md, doc_type="contract") == []


def test_desc_column_found_even_when_no_column_merged_into_it():
    """
    Regresi ke bug nyata (ditemukan dari benchmark KL FULL SIGNED dengan RapidOCR):
    tabel structure recognition RapidOCR kadang menggabungkan kolom "No." ke header
    tetangganya jadi satu sel, mis. "Uraian Pekerjaan/ Layanan. No" -- tidak terjadi
    dengan Apple Vision pada dokumen yang sama.

    Sebelumnya kata "no" ada di exclude-list desc_col, sehingga header gabungan ini
    ditolak PADAHAL jelas memuat kata kunci deskripsi ("pekerjaan", "layanan"), lalu
    jatuh ke fallback posisi tetap `headers[1]` -- yang kebetulan kolom Jumlah. Field
    'Deskripsi Item/Barang/Pekerjaan' pun terisi angka volume ("1") alih-alih nama
    barangnya, dan LLM "menambal" Spesifikasi dengan nama barang yang seharusnya
    ada di Deskripsi -- kolom-kolom hasil ekstraksi jadi tertukar.
    """
    headers = [
        "Uraian Pekerjaan/ Layanan. No",
        "Jumlah.",
        "Satuan.",
        "Jangka Waktu Pembayaran (bulan).",
        "Harga Satuan.",
        "Harga Satuan. MRC OTC",
        "Harga Total. MRC",
    ]
    cols = detect_item_columns(headers)
    assert cols["desc"] == "Uraian Pekerjaan/ Layanan. No"

    md = (
        "| Uraian Pekerjaan/ Layanan. No | Jumlah. | Satuan. | Jangka Waktu Pembayaran (bulan). "
        "| Harga Satuan. | Harga Satuan. MRC OTC | Harga Total. MRC |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 Penyediaan Fortigate 200f | 1 | Paket | 12 | | 20.790.000 | 249.480.000 |\n"
    )
    items = extract_items_from_markdown_tables(md, doc_type="contract")
    assert items[0]["Deskripsi Item/Barang/Pekerjaan"] == "Penyediaan Fortigate 200f"
    assert items[0]["Nomor Item"] == "1"
    assert items[0]["Jumlah Harga"] == 249480000.0


# ---------------------------------------------------------------- classifier
def test_classifier_sph_mentioning_spk_stays_sph():
    doc_type, conf = DocumentClassifier().classify_fast_rule(
        "SURAT PENAWARAN HARGA\nPerihal: sesuai SPK sebelumnya"
    )
    assert doc_type == "sph" and conf >= 0.95


# ---------------------------------------------------------------- evidence
def _ir(blocks):
    parsed = {
        "markdown": "\n".join(t for t, _ in blocks),
        "metadata": {
            "job_id": "t",
            "page_count": 1,
            "output_markdown_chars": 1,
            "parser_engine": "test",
        },
        "structure": {
            "children": [
                {
                    "type": "page",
                    "grounding": {
                        "page": 1,
                        "range": {"start": 0, "end": 1},
                        "box": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                    },
                    "children": [
                        {
                            "type": "paragraph",
                            "id": f"b{i}",
                            "text": t,
                            "grounding": {
                                "page": 1,
                                "range": {"start": 0, "end": 0},
                                "box": dict(zip(("xmin", "ymin", "xmax", "ymax"), box)),
                                "confidence": 0.95,
                            },
                        }
                        for i, (t, box) in enumerate(blocks)
                    ],
                }
            ]
        },
    }
    return from_parsed_response(parsed, "doc.pdf")


def test_address_with_single_number_is_not_a_number_match():
    score, match_type = calculate_match_confidence(
        "Jl. Contoh Raya No. 1, Bandung", "1. LINGKUP PEKERJAAN"
    )
    assert score == 0.0 and match_type is None


def test_small_numbers_do_not_ground_to_random_text():
    ir = _ir([("1. LINGKUP PEKERJAAN", (0.1, 0.1, 0.9, 0.2)), ("Tahun 2026", (0.1, 0.3, 0.9, 0.4))])
    assert locate_value(1.0, ir) is None


def test_float_amount_grounds_to_indonesian_formatted_number():
    ir = _ir([("Jumlah | 150.000.000", (0.1, 0.5, 0.9, 0.6))])
    match = locate_value(150000000.0, ir)
    assert match is not None and match.match_type == "number_match"


def test_multiline_address_grounds_across_blocks():
    ir = _ir(
        [
            ("Alamat : Kampus Universitas Contoh", (0.1, 0.20, 0.6, 0.22)),
            ("Jl. Merpati Raya No. 18, Semarang", (0.1, 0.23, 0.6, 0.25)),
        ]
    )
    match = locate_value("Kampus Universitas Contoh Jl. Merpati Raya No. 18, Semarang", ir)
    assert match is not None and "multiblock" in match.match_type
    assert match.bbox[1] == 0.20 and match.bbox[3] == 0.25


def test_landingai_style_blocks_use_markdown_range_for_text():
    parsed = {
        "markdown": "SURAT PERINTAH KERJA\nNomor : 045/SPK/2031",
        "metadata": {"job_id": "x", "page_count": 1, "output_markdown_chars": 40},
        "structure": {
            "children": [
                {
                    "type": "page",
                    "grounding": {
                        "page": 1,
                        "range": {"start": 0, "end": 40},
                        "box": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1},
                    },
                    "children": [
                        {
                            "type": "text",
                            "id": "t0",
                            "grounding": {
                                "page": 1,
                                "range": {"start": 21, "end": 41},
                                "box": {"xmin": 0.1, "ymin": 0.1, "xmax": 0.5, "ymax": 0.12},
                            },
                        }
                    ],
                }
            ]
        },
    }
    ir = from_parsed_response(parsed, "landing.pdf")
    assert ir.pages[0].blocks[0].text == "Nomor : 045/SPK/2031"
    assert ir.pages[0].blocks[0].block_type == "paragraph"


def test_evidence_status_reflects_validation_conflict():
    data = {
        "sub total": 1000000,
        "Total PPN": 110000,
        "Total Harga Pekerjaan": 1110000,
        "Jumlah Terbilang": "Dua Juta Rupiah",
        "Nomor Kontrak Kerja": "045/SPK/2031",
        "Nama Pekerjaan": "X",
        "Pihak Pertama": {"Nama Perusahaan": "A"},
        "Pihak Kedua": {"Nama Perusahaan": "B"},
    }
    ir = _ir(
        [
            ("Nomor : 045/SPK/2031", (0.1, 0.1, 0.5, 0.12)),
            ("Total 1.110.000 (Dua Juta Rupiah)", (0.1, 0.5, 0.9, 0.6)),
        ]
    )
    report = validate_contract(data)
    evidence = {e.field: e for e in build_field_evidence(data, ir, report)}
    assert evidence["Jumlah Terbilang"].status == FieldStatus.CONFLICT
    assert evidence["Nomor Kontrak Kerja"].status == FieldStatus.AUTO_VERIFIED
    # "A" tidak muncul di satu blok pun -> UNSUPPORTED, bukan REVIEW_REQUIRED.
    # Pembedaan ini yang membuat antrean review PM bisa diurutkan berdasar risiko: nilai
    # yang tidak ada di dokumen hampir selalu karangan model, dan harus dilihat lebih dulu
    # daripada nilai yang ketemu tapi buktinya lemah.
    assert evidence["Pihak Pertama.Nama Perusahaan"].status == FieldStatus.UNSUPPORTED


# ---------------------------------------------------------------- validation
def test_contract_validation_rules():
    ok = {
        "sub total": 150000000,
        "Total PPN": 16500000,
        "Total Harga Pekerjaan": 166500000,
        "persentase ppn": "11%",
        "Jumlah Terbilang": ("Seratus Enam Puluh Enam Juta Lima Ratus Ribu Rupiah"),
        "Nomor Kontrak Kerja": "687",
        "Nama Pekerjaan": "X",
        "Jangka Waktu": "29 Mei 2026 – 29 Mei 2027",
        "Pihak Pertama": {"Nama Perusahaan": "A", "Alamat": "Jl. A"},
        "Pihak Kedua": {"Nama Perusahaan": "B", "Alamat": "Jl. B"},
        "List Item/Barang": [{"volume": 1, "Harga Satuan": 150000000, "Jumlah Harga": 150000000}],
    }
    assert validate_contract(ok).status == "pass"

    bad = dict(
        ok,
        **{
            "Total PPN": 0,
            "Jumlah Terbilang": ("Seratus Enam Puluh Enam Juta Lima Ratus Rupiah"),
            "Pihak Kedua": {"Nama Perusahaan": "B", "Alamat": "Jl. A"},
        },
    )
    rules = {i.rule for i in validate_contract(bad).issues}
    assert {
        "total_equals_subtotal_plus_ppn",
        "terbilang_matches_amount",
        "parties_distinct",
    } <= rules


def test_sph_line_total_accepts_period_multiplier():
    data = {
        "Nomor SPH": "1",
        "Vendor": {"Nama Vendor": "PT X"},
        "Subtotal": 178500000,
        "Nilai PPN": 0,
        "Grand Total": 178500000,
        "Daftar Penawaran Harga": [
            {
                "Volume / Qty": 1,
                "Harga Satuan": 14875000,
                "Total Harga": 178500000,
                "Periode/Durasi": "12 Bulan",
            }
        ],
    }
    assert validate_sph(data).status == "pass"


# ---------------------------------------------------------------- docling bbox orientation
@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling tidak terpasang (dilewati di CI ringan)",
)
def test_docling_bbox_converted_to_top_left_origin():
    docling_base = pytest.importorskip("docling_core.types.doc.base")
    from app.parsers.docling_parser import DoclingParser

    # Kotak di bagian ATAS halaman (origin kiri-bawah: t besar).
    bbox = docling_base.BoundingBox(
        l=100, t=750, r=300, b=700, coord_origin=docling_base.CoordOrigin.BOTTOMLEFT
    )
    box = DoclingParser._normalize_bbox(bbox, 612, 792)
    assert box.ymin < 0.15 and box.ymax < 0.15


# ---------------------------------------------------------------- LLM client (tanpa jaringan)
def test_llm_options_set_context_window_and_seed():
    from app.config import config
    from app.extractors.ollama_client import OllamaExtractor

    options = OllamaExtractor()._options()
    assert options["num_ctx"] == config.OLLAMA_NUM_CTX and options["num_ctx"] >= 8192
    assert options["seed"] == config.OLLAMA_SEED
    assert options["temperature"] == 0.0


def test_terbilang_recovered_from_document_text():
    from app.extractors.ollama_client import OllamaExtractor

    md = (
        "sebesar Rp. 166.500.000,- (Seratus Enam Puluh Enam Juta Lima Ratus "
        "Ribu Rupiah) sudah termasuk PPN"
    )
    found = OllamaExtractor._find_terbilang_in_text(md, 166500000)
    assert found == "Seratus Enam Puluh Enam Juta Lima Ratus Ribu Rupiah"


def test_prompts_do_not_contain_evaluation_documents():
    # Nilai pembanding dibaca dari golden set lokal, bukan ditulis di sini: daftar nilai
    # asli di berkas tes sama saja membocorkannya ke repo publik. Hanya nilai yang memuat
    # angka. Cakupannya sama dengan daftar lama (nomor SPK, nomor SPH, alamat, total):
    # field Nomor/Alamat/Total. Field lain (NPWP, durasi, harga baris) belum diperiksa --
    # sebagian kebetulan sama dengan contoh di prompt dan perlu dipilah dengan `make eval`.
    import json

    from app.extractors import prompts

    goldens = sorted((ROOT / "eval" / "golden").glob("*.json"))
    if not goldens:
        pytest.skip("eval/golden/ tidak tersedia (data klien, tidak di git)")
    body = prompts.CONTRACT_EXTRACTION_SYSTEM_PROMPT + prompts.SPH_EXTRACTION_SYSTEM_PROMPT
    for path in goldens:
        fields = json.loads(path.read_text(encoding="utf-8")).get("fields", {})
        for name, value in fields.items():
            if (
                re.search(r"Nomor|Alamat|Total", name)
                and isinstance(value, str)
                and len(value) >= 6
                and re.search(r"\d", value)
            ):
                assert value not in body, f"{path.name}: {name} bocor ke prompt"


# ---------------------------------------------------------------- profiler & eval
def test_profiler_detects_native_pdf():
    sample = ROOT / "sample_pdfs" / "SPH Bapenda Jabar 2026.pdf"
    if not sample.exists():
        pytest.skip("sample PDF tidak tersedia")
    from app.ingestion.profiler import profile_document

    profile = profile_document(str(sample))
    assert profile.kind == "native" and not profile.needs_ocr


def test_eval_compare_rules():
    from eval.run_eval import compare

    assert compare(150000000, "150.000.000")[0] == "exact"
    assert compare("2026-06-02", "2 Juni 2026")[0] == "exact"
    assert compare("Seratus Ribu Rupiah", "Seratus Rupiah")[0] == "wrong"
    assert compare("1/1000", "1/1000 (satu per mil)")[0] == "lenient"
    assert compare("X", None)[0] == "missing"


def test_engine_refuses_llm_extraction_on_image_only_parse():
    from app.schemas.common import DocumentStructure, LandingAIParsedResponse, ParseMetadata
    from app.services.engine import OpenADEEngine, ParsingError

    parsed = LandingAIParsedResponse(
        markdown="\n\n".join(f"[IMAGE: detected at page {i}]" for i in range(1, 15)),
        metadata=ParseMetadata(
            job_id="x", page_count=14, output_markdown_chars=664, parser_engine="ppstructure+slanet"
        ),
        structure=DocumentStructure(children=[]),
    )
    with pytest.raises(ParsingError):
        OpenADEEngine.ensure_enough_text(parsed)


def test_llm_output_is_capped():
    from app.extractors.ollama_client import OllamaExtractor

    assert OllamaExtractor()._options()["num_predict"] > 0


# ---------------------------------------------------------------- generalization: doc types beyond
# samples
def test_classifier_detects_pks_and_nota_pesanan():
    pks_doc = (
        "PERJANJIAN KERJA SAMA\nNomor: 12/PKS/2031\nantara PT A dan PT B tentang layanan jaringan"
    )
    nota_doc = (
        "NOTA PESANAN\nNomor: 045/NP/2031\nKepada: PT Vendor Contoh\nBerikut kami pesan "
        "barang sebagai berikut:"
    )
    c = DocumentClassifier()
    assert c.classify_fast_rule(pks_doc)[0] == "contract"
    assert c.classify_fast_rule(nota_doc)[0] == "contract"


def test_party_extraction_handles_pihak_kesatu_synonym():
    md = (
        "PERJANJIAN KERJA SAMA\nPada hari ini ... kami yang bertanda tangan dibawah ini:\n"
        "Nama : Andi Wijaya\nJabatan : Kepala Bagian Pengadaan\nAlamat : Jl. Diponegoro No. 5, "
        "Malang\n"
        "Yang dalam hal ini mewakili secara sah : PT CONTOH SATU, selanjutnya disebut sebagai "
        "PIHAK KESATU, dan\n"
        "Nama : Budi Santoso\nJabatan : Direktur\nAlamat : Jl. Sudirman No. 9, Malang\n"
        "Yang dalam hal ini mewakili secara sah : CV CONTOH DUA, selanjutnya disebut sebagai "
        "PIHAK KEDUA"
    )
    parties = ContextAnalyzer.extract_parties_from_preamble(md)
    assert parties["pihak_pertama"]["nama_perusahaan"] == "PT CONTOH SATU"
    assert parties["pihak_kedua"]["nama_perusahaan"] == "CV CONTOH DUA"


def test_contract_schema_allows_minimal_nota_pesanan_without_hallucination_fields():
    """
    Nota Pesanan seringkali hanya berisi: pemesan, penerima pesanan, daftar barang, total.
    Tanpa blok tanda tangan formal (jabatan/alamat pejabat) atau nomor kontrak resmi.
    Schema tidak boleh memaksa field itu diisi string -- harus bisa null.
    """
    from app.schemas.contract import ContractExtractionSchema

    minimal = {
        "Pihak Pertama": {
            "Nama Perusahaan": "PT Pemesan Contoh"
        },  # tanpa representative/jabatan/alamat
        "Pihak Kedua": {"Nama Perusahaan": "CV Vendor Contoh"},
        "List Item/Barang": [
            {
                "Deskripsi Item/Barang/Pekerjaan": "Kabel UTP Cat6",
                "volume": 100,
                "unit": "meter",
                "Harga Satuan": 15000,
                "Jumlah Harga": 1500000,
            }
        ],
        # nomor_kontrak, nama_pekerjaan, sub_total, total_harga_pekerjaan sengaja tidak diisi
    }
    extracted = ContractExtractionSchema.model_validate(minimal)
    assert extracted.nomor_kontrak is None
    assert extracted.pihak_pertama.jabatan is None
    assert extracted.pihak_pertama.alamat is None
    assert extracted.sub_total is None
    assert extracted.total_harga_pekerjaan is None


def test_skema_tidak_mengarang_ppn_saat_dokumen_tidak_menyebutkannya():
    """
    Dokumen yang tidak mencantumkan PPN harus keluar dengan PPN null -- bukan "11%" dan
    bukan 0.0.

    Dulu keduanya punya nilai default di skema, sehingga setiap dokumen selalu membawa
    tarif dan nominal PPN walaupun tidak satu baris pun di dokumennya menyebut PPN. Nilai
    karangan itu tidak punya pasal untuk dikonfirmasi PM (briefing hlm. 11), dan di NocoDB
    ia terlihat sama meyakinkannya dengan nilai yang benar-benar terbaca.
    """
    from app.schemas.contract import ContractExtractionSchema
    from app.schemas.sph import SPHExtractionSchema

    kontrak = ContractExtractionSchema.model_validate(
        {
            "Pihak Pertama": {"Nama Perusahaan": "PT A"},
            "Pihak Kedua": {"Nama Perusahaan": "PT B"},
            "List Item/Barang": [],
        }
    )
    assert kontrak.persentase_ppn is None, "tarif PPN dikarang padahal dokumen tidak menyebutnya"
    assert kontrak.total_ppn is None, "nominal PPN dikarang padahal dokumen tidak menyebutnya"

    sph = SPHExtractionSchema.model_validate(
        {
            "Vendor": {"Nama Vendor": "PT V"},
            "Nomor SPH": "1",
            "Tanggal SPH": "2026-01-01",
            "Subtotal": 1000.0,
            "Grand Total": 1000.0,
        }
    )
    assert sph.persentase_ppn is None and sph.ppn_nominal is None


def test_total_ppn_tidak_pernah_dihitung_dari_selisih():
    """
    `extract_contract` dulu mengisi Total PPN dengan (Total - Sub Total). Itu membuat aturan
    validasi "sub total + Total PPN = Total Harga" selalu lolos -- aturan itu jadi memeriksa
    hasil hitungannya sendiri, bukan isi dokumen.

    Tes ini membaca sumbernya: kalau rumus itu kembali muncul di jalur ekstraksi, ia gagal.
    """
    import inspect

    from app.extractors import ollama_client

    sumber = inspect.getsource(ollama_client)
    # Cari penugasan ke total_ppn dari hasil pengurangan, dalam bentuk apa pun.
    pola = re.compile(r"total_ppn\s*=\s*(?!None).*?-\s*.*?sub_total", re.DOTALL)
    cocok = pola.search(sumber)
    assert cocok is None, (
        "Total PPN kembali dihitung dari selisih total dan sub total. Nilai hasil hitungan "
        "tidak ada di dokumen, jadi tidak bisa dikonfirmasi PM per field. Selisihnya "
        "dilaporkan app/validation/rules.py sebagai temuan, bukan diisikan diam-diam."
    )


def test_validasi_total_melaporkan_selisih_yang_dulu_ditutupi():
    """
    Konsekuensi positif dari berhenti menghitung: aturan total kini benar-benar menguji
    dokumen. Sub total + PPN yang tidak sama dengan Total harus jadi temuan ERROR.
    """
    from app.validation.rules import validate_extraction

    hasil = validate_extraction(
        "contract",
        {
            "sub total": 100_000.0,
            "Total PPN": 11_000.0,
            "Total Harga Pekerjaan": 200_000.0,  # sengaja tidak cocok
            "persentase ppn": "11%",
        },
    )
    temuan = [i for i in hasil.issues if i.rule == "total_equals_subtotal_plus_ppn"]
    assert temuan, "selisih total tidak dilaporkan"


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None,
    reason="docling tidak terpasang (dilewati di CI ringan)",
)
def test_docling_converter_allows_non_pdf_formats_by_default():
    """
    format_options hanya menimpa opsi untuk PDF; format lain (DOCX, gambar) tetap
    memakai default Docling, bukan dibatasi hanya-PDF.
    """
    from docling.datamodel.base_models import InputFormat

    from app.parsers.docling_parser import DoclingParser

    converter = DoclingParser()._get_converter(do_ocr=False)
    assert InputFormat.DOCX in converter.allowed_formats
    assert InputFormat.IMAGE in converter.allowed_formats


# ---------------------------------------------------------------- BAST (Berita Acara Serah Terima)
# Ditambahkan setelah eksplorasi "Data Project BUT": 18 proyek nyata, masing-masing punya
# Kontrak/SPK/PKS/Nota-Pesanan + SPH + BAST. BAST ternyata dokumen terpisah dengan struktur
# sendiri (bukan varian kontrak): tabel TANPA kolom harga, kadang memuat bagian tambahan
# "Berita Acara Uji Terima". Teks contoh di bawah diparafrasakan dari pola yang berulang di
# seluruh 18 proyek tsb, bukan salinan persis satu klien.

BAST_SAMPLE_MD = """BERITA ACARA SERAH TERIMA (BAST)

Nama Pekerjaan Pengadaan Access Point Kebutuhan Dinas Contoh
Tanggal PO / Kontrak 10 Januari 2031
Nomor PO / Kontrak 210/00/BIS-01/BUT/2031

Pada hari ini, Senin Tanggal Sepuluh Bulan Februari Tahun 2031, kami yang bertanda tangan di bawah ini:

PIHAK PERTAMA
Nama Rina Kartika
Perusahaan Dinas Komunikasi dan Informatika
Jabatan Kepala Bidang Infrastruktur

PIHAK KEDUA
Nama Bayu Pratama
Perusahaan PT Contoh Jaringan Sejahtera
Jabatan Direktur

dengan rincian sebagai berikut:

| No | Deskripsi | Volume | Satuan | Keterangan |
|---|---|---|---|---|
| 1 | Access Point Wi-Fi 6 Indoor | 10 | Unit | |
| 2 | Kabel UTP Cat6 | 100 | Meter | |

Dokumen pendukung:
1. Purchase Order (PO)/Kontrak
2. Delivery Order (DO)/Surat Jalan

PIHAK KEDUA menyatakan bahwa barang telah diterima dalam kondisi baik, lengkap, dan sesuai dengan dokumen PO/Kontrak."""  # noqa: E501 (teks contoh dokumen)


def test_classifier_detects_bast():
    doc_type, conf = DocumentClassifier().classify_fast_rule(BAST_SAMPLE_MD)
    assert doc_type == "bast" and conf >= 0.95


def test_table_extractor_handles_bast_tables_without_price_columns():
    from app.parsers.table_extractor import extract_items_from_markdown_tables

    items = extract_items_from_markdown_tables(BAST_SAMPLE_MD, doc_type="bast")
    assert len(items) == 2
    assert items[0]["No"] == "1"
    assert items[0]["Deskripsi"] == "Access Point Wi-Fi 6 Indoor"
    assert items[0]["Volume"] == 10.0
    assert items[0]["Satuan"] == "Unit"
    assert items[1]["No"] == "2"


def test_bast_schema_validates_full_and_minimal_data():
    from app.schemas.bast import BASTExtractionSchema

    full = {
        "Nomor BAST": None,
        "Nama Pekerjaan": "Pengadaan Access Point",
        "Nomor PO / Kontrak": "210/00/BIS-01/BUT/2031",
        "Tanggal PO / Kontrak": "10 Januari 2031",
        "Tanggal Serah Terima": "10 Februari 2031",
        "Pihak Pertama": {
            "Nama Perusahaan": "Dinas Contoh",
            "Nama Representative": "Rina Kartika",
            "Jabatan": "Kepala Bidang",
            "Alamat": None,
        },
        "Pihak Kedua": {
            "Nama Perusahaan": "PT Contoh Jaringan Sejahtera",
            "Nama Representative": "Bayu Pratama",
            "Jabatan": "Direktur",
            "Alamat": None,
        },
        "Daftar Barang/Pekerjaan Diserahkan": [
            {"No": "1", "Deskripsi": "Access Point", "Volume": 10, "Satuan": "Unit"}
        ],
        "Nilai Pengadaan": None,
        "Pernyataan Penerimaan": "diterima dalam kondisi baik",
        "Tanggal Uji Terima": None,
        "Hasil Uji Terima Keseluruhan": None,
        "Dokumen Pendukung": ["PO/Kontrak"],
    }
    extracted = BASTExtractionSchema.model_validate(full)
    assert extracted.nomor_bast is None and extracted.items[0].deskripsi == "Access Point"

    # Minimal: BAST tanpa nomor sendiri dan tanpa detail pejabat -- harus tetap valid (tidak crash).
    minimal = {
        "Pihak Pertama": {"Nama Perusahaan": "Dinas Contoh"},
        "Pihak Kedua": {"Nama Perusahaan": "PT Contoh"},
        "Daftar Barang/Pekerjaan Diserahkan": [],
    }
    extracted_min = BASTExtractionSchema.model_validate(minimal)
    assert extracted_min.items == [] and extracted_min.pihak_pertama.jabatan is None


def test_validate_bast_flags_identical_parties_and_bad_date_order():
    data = {
        "Pihak Pertama": {"Nama Perusahaan": "PT Sama", "Alamat": "Jl. Sama"},
        "Pihak Kedua": {"Nama Perusahaan": "PT Sama", "Alamat": "Jl. Sama"},
        "Tanggal PO / Kontrak": "10 Februari 2031",
        "Tanggal Serah Terima": "5 Januari 2031",
        "Daftar Barang/Pekerjaan Diserahkan": [{"Deskripsi": "Item A"}],
    }
    from app.validation.rules import validate_bast

    report = validate_bast(data)
    rules = {i.rule for i in report.issues}
    assert "parties_distinct" in rules
    assert "serah_terima_after_po_kontrak" in rules
    assert report.status == "fail"


def test_validate_bast_passes_on_clean_document():
    from app.validation.rules import validate_bast

    data = {
        "Pihak Pertama": {"Nama Perusahaan": "Dinas Contoh", "Alamat": "Jl. A"},
        "Pihak Kedua": {"Nama Perusahaan": "PT Vendor Contoh", "Alamat": "Jl. B"},
        "Nomor PO / Kontrak": "210/00/BIS-01/BUT/2031",
        "Nama Pekerjaan": "Pengadaan Access Point",
        "Tanggal PO / Kontrak": "10 Januari 2031",
        "Tanggal Serah Terima": "10 Februari 2031",
        "Daftar Barang/Pekerjaan Diserahkan": [
            {"Deskripsi": "Access Point", "Volume": 10, "Satuan": "Unit"}
        ],
    }
    assert validate_bast(data).status == "pass"


def test_engine_dispatches_bast_extraction_without_llm_call():
    """engine.extract() harus memanggil extractor.extract_bast(), bukan jatuh ke kontrak."""
    from app.schemas.bast import BASTExtractionSchema
    from app.services.engine import OpenADEEngine

    class _FakeExtractor:
        llm_calls = 0
        llm_seconds = 0.0

        def reset_stats(self):
            pass

        def extract_bast(self, markdown_text):
            return BASTExtractionSchema.model_validate(
                {
                    "Pihak Pertama": {"Nama Perusahaan": "A"},
                    "Pihak Kedua": {"Nama Perusahaan": "B"},
                    "Daftar Barang/Pekerjaan Diserahkan": [],
                }
            )

    engine = OpenADEEngine()
    engine._extractor = _FakeExtractor()
    extracted, resolved = engine.extract(BAST_SAMPLE_MD, doc_type="bast")
    assert resolved == "bast"
    assert isinstance(extracted, BASTExtractionSchema)


def test_classifier_detects_spmk_as_contract_family():
    """
    SPMK (Surat Perintah Mulai Kerja) -- ditemukan di 'Data Project BUT': perintah resmi
    mulai bekerja yang diterbitkan setelah kontrak ditandatangani. User mengonfirmasi ini
    harus dialurkan sebagai dokumen keluarga kontrak (skema yang sama seperti SPK), bukan
    tipe terpisah. Sebelum perbaikan, skornya 0 di semua tipe (default fallback 0.70).
    """
    text = (
        "Perihal : Surat Perintah Mulai Kerja Pengadaan Perangkat Uji 1000D Kebutuhan Instansi B\n"
        "PT Vendor A kami tunjuk untuk melaksanakan proses pekerjaan Pengadaan Perangkat Uji "
        "1000D"
    )
    doc_type, conf = DocumentClassifier().classify_fast_rule(text)
    assert doc_type == "contract" and conf >= 0.95
