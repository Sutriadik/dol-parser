"""
box_inside_regions: kotak OCR hanya dianggap "sudah tertangkap" bila tumpang-tindih
dengan region di DUA sumbu. Dulu hanya sumbu vertikal yang dicek, sehingga teks kolom
kanan yang sebaris dengan region kolom kiri ikut dibuang.
"""

from types import SimpleNamespace

from app.parsers.layout_parser import LayoutRegion, box_inside_regions

W, H = 1000, 1400
KIRI = LayoutRegion(region_type="text", bbox=[50, 1000, 450, 1200])


def _box(x1, y1, x2, y2):
    return SimpleNamespace(xmin=x1, ymin=y1, xmax=x2, ymax=y2)


def test_kotak_di_dalam_region_tertangkap():
    assert box_inside_regions(_box(100, 1050, 400, 1080), [KIRI], W, H)


def test_kotak_sebaris_di_kolom_lain_tidak_dianggap_tertangkap():
    """Blok tanda tangan kanan sejajar blok kiri -- harus dipulihkan, bukan dibuang."""
    assert not box_inside_regions(_box(600, 1050, 900, 1080), [KIRI], W, H)


def test_koordinat_ternormalisasi_diubah_ke_piksel():
    assert box_inside_regions(_box(0.1, 0.75, 0.4, 0.77), [KIRI], W, H)
    assert not box_inside_regions(_box(0.6, 0.75, 0.9, 0.77), [KIRI], W, H)


def test_toleransi_menangkap_kotak_di_tepi_region():
    assert box_inside_regions(_box(440, 1050, 460, 1080), [KIRI], W, H)
