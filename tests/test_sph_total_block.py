"""
Blok ringkasan harga SPH yang OCR-nya terpecah -- kasus nyata SPH PT Vendor A.

Rantai kegagalan yang dijaga tes ini:
1. Docling mengeluarkan potongan "PPN 11% / 61,25 / Total Beban Pekerjaan / 3 / ,708 /
   618,105,600" dalam urutan melompat, lalu LLM menyambung digitnya jadi 6.125.708.618.
2. Sanity guard "memperbaikinya" dengan subtotal x tarif -- angka yang tidak ada di dokumen.
3. Karena angka karangan itu konsisten, validasi aritmetika meloloskannya sebagai `pass`.
"""

from types import SimpleNamespace

from app.extractors.ollama_client import OllamaExtractor
from app.parsers.block_grouper import DraftBlock, merge_adjacent_blocks
from app.schemas.evidence import FieldStatus
from app.validation.rules import check_amounts_grounded, validate_sph


def _b(text, xmin, ymin, xmax, ymax):
    return DraftBlock(type="paragraph", text=text, bbox=(xmin, ymin, xmax, ymax), confidence=0.9)


# Urutan dan koordinat persis seperti keluaran Docling untuk halaman 1 SPH PT Vendor A.
SPH_VENDOR = [
    _b("PPN 11%", 0.15836, 0.53764, 0.24354, 0.54936),
    _b("61,25", 0.76227, 0.53764, 0.81887, 0.54936),
    _b("Total Beban Pekerjaan", 0.15836, 0.5583, 0.3619, 0.57002),
    _b("3", 0.81887, 0.53781, 0.82915, 0.54933),
    _b(",708", 0.82915, 0.53764, 0.87032, 0.54936),
    _b("618,105,600", 0.76227, 0.5583, 0.87032, 0.57002),
]


# --------------------------------------------------------------------- lapisan 1: urutan baca
def test_blok_total_terpecah_disusun_per_baris_dan_angkanya_utuh():
    merged = merge_adjacent_blocks(SPH_VENDOR)
    assert len(merged) == 1
    assert merged[0].text == "PPN 11% 61,253,708\nTotal Beban Pekerjaan 618,105,600"


def test_paragraf_berurutan_normal_tidak_diubah():
    """Urutan mentah yang sudah menurun tidak disentuh sama sekali."""
    blocks = [
        _b("Baris pertama paragraf", 0.1, 0.10, 0.9, 0.11),
        _b("baris kedua paragraf", 0.1, 0.115, 0.9, 0.125),
    ]
    assert merge_adjacent_blocks(blocks)[0].text == "Baris pertama paragraf\nbaris kedua paragraf"


def test_prosa_panjang_tidak_diselang_seling_walau_urutan_melompat():
    """Dua kolom prosa (baris panjang) tidak boleh disusun ulang per baris visual."""
    kiri = "Kolom kiri berisi kalimat yang cukup panjang untuk jadi prosa"
    kanan = "Kolom kanan juga berisi kalimat yang panjang sekali isinya"
    blocks = [
        _b(kiri, 0.05, 0.200, 0.45, 0.210),
        _b(kiri + " lanjut", 0.05, 0.212, 0.45, 0.222),
        _b(kanan, 0.55, 0.200, 0.95, 0.210),
    ]
    assert merge_adjacent_blocks(blocks)[0].text.split("\n")[0] == kiri


def test_angka_tidak_bersentuhan_dipisah_spasi():
    """Dua angka di kolom berbeda (Qty dan Harga) tidak boleh tersambung jadi satu."""
    blocks = [
        _b("Total", 0.1, 0.30, 0.2, 0.31),
        _b("1.000", 0.6, 0.32, 0.7, 0.33),
        _b("2", 0.4, 0.30, 0.42, 0.31),
        _b("5.000", 0.6, 0.30, 0.7, 0.31),
    ]
    lines = merge_adjacent_blocks(blocks)[0].text.split("\n")
    assert lines[0] == "Total 2 5.000"


# --------------------------------------------------------------------- lapisan 2: tanpa karangan
def test_guard_mengosongkan_bukan_menghitung_ulang():
    ext = SimpleNamespace(
        subtotal=604_800_000.0,
        persentase_ppn="11%",
        ppn_nominal=6_125_708_618.1056,
        grand_total=6_125_708_618.1056,
    )
    OllamaExtractor._sanitize_sph_totals(ext)
    assert ext.ppn_nominal is None
    assert ext.grand_total is None


def test_guard_membiarkan_nilai_wajar():
    ext = SimpleNamespace(
        subtotal=604_800_000.0,
        persentase_ppn="11%",
        ppn_nominal=61_253_708.0,
        grand_total=618_105_600.0,
    )
    OllamaExtractor._sanitize_sph_totals(ext)
    assert ext.ppn_nominal == 61_253_708.0
    assert ext.grand_total == 618_105_600.0


# --------------------------------------------------------------------- lapisan 3: validasi
def _ev(field, value, status):
    return SimpleNamespace(field=field, value=value, status=status)


def test_nominal_tidak_ditemukan_di_dokumen_tidak_lolos_pass():
    """Angka karangan yang saling konsisten lolos aritmetika -- grounding harus menahannya."""
    data = {
        "Nomor SPH": "01/SPH",
        "Vendor": {"Nama Vendor": "PT Vendor A"},
        "Subtotal": 604_800_000,
        "Persentase PPN": "11%",
        "Nilai PPN": 66_528_000,
        "Grand Total": 671_328_000,
    }
    report = validate_sph(data)
    assert report.status == "pass"  # aritmetika memang konsisten

    evidence = [
        _ev("Subtotal", 604_800_000, FieldStatus.AUTO_VERIFIED),
        _ev("Nilai PPN", 66_528_000, FieldStatus.UNSUPPORTED),
        _ev("Grand Total", 671_328_000, FieldStatus.UNSUPPORTED),
    ]
    report = check_amounts_grounded(report, "sph", evidence)
    assert report.status == "warn"
    assert {f for i in report.issues for f in i.fields} == {"Nilai PPN", "Grand Total"}
    assert "amounts_grounded" in report.checked_rules


def test_nilai_tertulis_di_dokumen_tapi_tidak_konsisten_ditandai_gagal():
    """Nilai asli vendor A: 604,8 jt + 61,25 jt != 618,1 jt. Dokumennya sendiri tidak
    konsisten -- hasil yang jujur adalah `fail` (PM meninjau), bukan `pass`."""
    data = {
        "Nomor SPH": "01/SPH",
        "Vendor": {"Nama Vendor": "PT Vendor A"},
        "Subtotal": 604_800_000,
        "Persentase PPN": "11%",
        "Nilai PPN": 61_253_708,
        "Grand Total": 618_105_600,
    }
    assert validate_sph(data).status == "fail"
