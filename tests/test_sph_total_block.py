"""
Blok ringkasan harga SPH yang OCR-nya terpecah -- kasus nyata SPH PT Vendor A.

Rantai kegagalan yang dijaga tes ini:
1. Docling mengeluarkan potongan "PPN 11% / 52,43 / Total Beban Pekerjaan / 7 / ,916 /
   533,218,400" dalam urutan melompat, lalu LLM menyambung digitnya jadi 5.243.916.533.
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


# Urutan dan koordinat persis seperti keluaran Docling untuk halaman 1 SPH PT Vendor A;
# digitnya diganti angka rekaan dengan pola potongan yang sama.
SPH_VENDOR = [
    _b("PPN 11%", 0.15836, 0.53764, 0.24354, 0.54936),
    _b("52,43", 0.76227, 0.53764, 0.81887, 0.54936),
    _b("Total Beban Pekerjaan", 0.15836, 0.5583, 0.3619, 0.57002),
    _b("7", 0.81887, 0.53781, 0.82915, 0.54933),
    _b(",916", 0.82915, 0.53764, 0.87032, 0.54936),
    _b("533,218,400", 0.76227, 0.5583, 0.87032, 0.57002),
]


# --------------------------------------------------------------------- lapisan 1: urutan baca
def test_blok_total_terpecah_disusun_per_baris_dan_angkanya_utuh():
    merged = merge_adjacent_blocks(SPH_VENDOR)
    assert len(merged) == 1
    assert merged[0].text == "PPN 11% 52,437,916\nTotal Beban Pekerjaan 533,218,400"


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
        subtotal=519_600_000.0,
        persentase_ppn="11%",
        ppn_nominal=5_243_916_533.2184,
        grand_total=5_243_916_533.2184,
    )
    OllamaExtractor._sanitize_sph_totals(ext)
    assert ext.ppn_nominal is None
    assert ext.grand_total is None


def test_guard_membiarkan_nilai_wajar():
    ext = SimpleNamespace(
        subtotal=519_600_000.0,
        persentase_ppn="11%",
        ppn_nominal=52_437_916.0,
        grand_total=533_218_400.0,
    )
    OllamaExtractor._sanitize_sph_totals(ext)
    assert ext.ppn_nominal == 52_437_916.0
    assert ext.grand_total == 533_218_400.0


# --------------------------------------------------------------------- lapisan 3: validasi
def _ev(field, value, status):
    return SimpleNamespace(field=field, value=value, status=status)


def test_nominal_tidak_ditemukan_di_dokumen_tidak_lolos_pass():
    """Angka karangan yang saling konsisten lolos aritmetika -- grounding harus menahannya."""
    data = {
        "Nomor SPH": "01/SPH",
        "Vendor": {"Nama Vendor": "PT Vendor A"},
        "Subtotal": 519_600_000,
        "Persentase PPN": "11%",
        "Nilai PPN": 57_156_000,
        "Grand Total": 576_756_000,
    }
    report = validate_sph(data)
    assert report.status == "pass"  # aritmetika memang konsisten

    evidence = [
        _ev("Subtotal", 519_600_000, FieldStatus.AUTO_VERIFIED),
        _ev("Nilai PPN", 57_156_000, FieldStatus.UNSUPPORTED),
        _ev("Grand Total", 576_756_000, FieldStatus.UNSUPPORTED),
    ]
    report = check_amounts_grounded(report, "sph", evidence)
    assert report.status == "warn"
    assert {f for i in report.issues for f in i.fields} == {"Nilai PPN", "Grand Total"}
    assert "amounts_grounded" in report.checked_rules


def test_nilai_tertulis_di_dokumen_tapi_tidak_konsisten_ditandai_gagal():
    """Pola nilai vendor A (angka rekaan): 519,6 jt + 52,44 jt != 533,2 jt. Dokumennya
    sendiri tidak konsisten -- hasil yang jujur adalah `fail` (PM meninjau), bukan `pass`."""
    data = {
        "Nomor SPH": "01/SPH",
        "Vendor": {"Nama Vendor": "PT Vendor A"},
        "Subtotal": 519_600_000,
        "Persentase PPN": "11%",
        "Nilai PPN": 52_437_916,
        "Grand Total": 533_218_400,
    }
    assert validate_sph(data).status == "fail"


# --------------------------------------------------------------------- lapisan 4: nominal LLM
def test_subtotal_llm_yang_tidak_tertulis_di_dokumen_dikosongkan():
    """
    SPH jasa: dokumen hanya menulis "Jumlah Setelah PPN" tanpa baris subtotal, tetapi LLM
    mengisi Subtotal dengan angka lain. Angka itu tidak ada di dokumen; tidak boleh
    terkirim ke PM sebagai isian awal, dan tidak boleh diganti hasil hitungan.
    """
    md = (
        "| Uraian | Jumlah (Rp) |\n|---|---|\n| Jasa Contoh | 14.250.000 |\n"
        "| Jumlah Setelah PPN | 14.250.000 |\n"
    )
    ext = SimpleNamespace(subtotal=12_800_000.0, ppn_nominal=None, grand_total=14_250_000.0)
    OllamaExtractor._drop_unwritten_sph_totals(ext, md)
    assert ext.subtotal is None
    assert ext.grand_total == 14_250_000.0


def test_nominal_yang_hanya_tertulis_sebagai_terbilang_dibiarkan():
    md = "Total penawaran (Empat Belas Juta Dua Ratus Lima Puluh Ribu Rupiah)."
    ext = SimpleNamespace(subtotal=None, ppn_nominal=None, grand_total=14_250_000.0)
    OllamaExtractor._drop_unwritten_sph_totals(ext, md)
    assert ext.grand_total == 14_250_000.0


def test_nominal_tertulis_berformat_lain_dibiarkan():
    md = "Sub Total Rp 519,600,000\nPPN 11% Rp. 52.437.916,-\nTotal 533.218.400,00"
    ext = SimpleNamespace(
        subtotal=519_600_000.0, ppn_nominal=52_437_916.0, grand_total=533_218_400.0
    )
    OllamaExtractor._drop_unwritten_sph_totals(ext, md)
    assert (ext.subtotal, ext.ppn_nominal, ext.grand_total) == (
        519_600_000.0,
        52_437_916.0,
        533_218_400.0,
    )
