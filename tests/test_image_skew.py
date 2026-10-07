"""
Deteksi kemiringan halaman scan (image_enhancer.estimate_skew_angle).

Regresi: di OpenCV 5 HoughLinesP mengembalikan (N, 4), bukan (N, 1, 4). Kode lama membaca
line[0] sebagai (x1, y1, x2, y2), unpack-nya gagal, dan except menelannya -- sudut selalu
0.0 sehingga deskew tidak pernah berjalan pada dokumen apa pun.
"""

from unittest import mock

import cv2
import numpy as np
import pytest

from app.parsers import image_enhancer as ie


def _halaman_miring(derajat: float) -> np.ndarray:
    img = np.full((1400, 1000), 255, np.uint8)
    for y in range(120, 1300, 45):
        cv2.putText(
            img,
            "Nilai kontrak Rp 738.150.000 termasuk PPN 11 persen",
            (60, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            0,
            2,
        )
    m = cv2.getRotationMatrix2D((500, 700), derajat, 1.0)
    return cv2.warpAffine(img, m, (1000, 1400), borderValue=255)


def _sisa_setelah_deskew(gray: np.ndarray) -> float:
    lurus = ie.deskew_image(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), ie.estimate_skew_angle(gray))
    return ie.estimate_skew_angle(cv2.cvtColor(lurus, cv2.COLOR_BGR2GRAY))


@pytest.mark.parametrize("derajat", [-6, -3, 3, 6])
def test_kemiringan_terdeteksi_dan_diluruskan(derajat):
    gray = _halaman_miring(derajat)
    assert ie.estimate_skew_angle(gray) == pytest.approx(-derajat, abs=0.2)
    assert abs(_sisa_setelah_deskew(gray)) < 0.3


@pytest.mark.parametrize("bentuk", ["opencv4", "opencv5"])
def test_kedua_bentuk_keluaran_hough_didukung(bentuk):
    asli = cv2.HoughLinesP

    def hough(*a, **kw):
        lines = asli(*a, **kw)
        return lines.reshape(-1, 1, 4) if bentuk == "opencv4" else lines.reshape(-1, 4)

    with mock.patch.object(ie.cv2, "HoughLinesP", side_effect=hough):
        assert ie.estimate_skew_angle(_halaman_miring(3)) == pytest.approx(-3, abs=0.2)


@pytest.mark.parametrize("derajat", [-4, 4])
def test_jalur_cadangan_saat_hough_tidak_menemukan_garis(derajat):
    with mock.patch.object(ie.cv2, "HoughLinesP", return_value=None):
        assert ie.estimate_skew_angle(_halaman_miring(derajat)) == pytest.approx(-derajat, abs=0.2)


def test_halaman_lurus_tidak_diputar():
    gray = _halaman_miring(0)
    assert ie.estimate_skew_angle(gray) == pytest.approx(0, abs=0.1)
    bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    assert ie.deskew_image(bgr, 0.0) is bgr
