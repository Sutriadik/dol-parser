"""
Open ADE — Image Quality Enhancement Layer
Modul preprocessing citra dokumen scanned & image-based PDF:
1. Adaptive Deskewing (Koreksi kemiringan teks)
2. Shadow & Lighting Normalization (Penghapusan bayangan dan gradien pencahayaan)
3. CLAHE (Contrast Limited Adaptive Histogram Equalization)
4. Edge & Character Sharpening (Unsharp Masking untuk ketajaman angka & teks halus)
5. High-Resolution 300 DPI Rendering
"""

import math

import cv2
import numpy as np
import pymupdf as fitz

from app.logger import logger


def _normalize_angle(angle: float) -> float:
    """Sudut garis -> rentang (-45, 45]: kemiringan baris teks, bukan arah garisnya."""
    while angle > 45:
        angle -= 90
    while angle <= -45:
        angle += 90
    return angle


def estimate_skew_angle(gray_img: np.ndarray) -> float:
    """
    Mengestimasi sudut kemiringan (derajat) dokumen menggunakan Hough Lines & Text Contours.

    Konvensi sudut: koordinat gambar (y ke bawah), sama untuk kedua jalur, dan langsung bisa
    diberikan ke deskew_image.
    """
    try:
        # Otsu thresholding terbalik (teks jadi putih, latar jadi hitam)
        _, thresh = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # Dilasi horizontal untuk menghubungkan karakter dalam 1 baris
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (30, 3))
        dilated = cv2.dilate(thresh, kernel, iterations=1)

        # Deteksi garis dengan HoughLinesP
        lines = cv2.HoughLinesP(
            dilated, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=20
        )

        angles = []
        if lines is not None:
            # OpenCV 4 mengembalikan (N, 1, 4), OpenCV 5 (N, 4). Dulu kode ini membaca
            # line[0] sebagai (x1, y1, x2, y2); di OpenCV 5 itu hanya satu angka, unpack-nya
            # gagal, dan except di bawah menelannya -- deskew tidak pernah berjalan.
            for x1, y1, x2, y2 in lines.reshape(-1, 4):
                if x2 - x1 == 0:
                    continue
                angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
                # Ambil hanya sudut kemiringan teks horizontal (-45 sampai +45 derajat)
                if -45 < angle < 45:
                    angles.append(angle)

        if angles:
            return float(np.median(angles))

        # Fallback: kotak terkecil yang melingkupi semua piksel teks. Titik harus (x, y)
        # float32 -- np.where memberi (baris, kolom) -- dan sudut minAreaRect dinormalisasi
        # dari sisi panjangnya, karena konvensi sudutnya berubah antar-versi OpenCV
        # (4.5.1 dan 5.x tidak sama dengan versi lama yang dipakai rumus sebelumnya).
        ys, xs = np.where(thresh > 0)
        if len(xs) > 50:
            (_, _), (w, h), angle = cv2.minAreaRect(np.column_stack((xs, ys)).astype(np.float32))
            if w < h:
                angle += 90
            angle = _normalize_angle(angle)
            return float(angle) if abs(angle) < 45 else 0.0

    except Exception as e:
        # Bukan debug: kegagalan diam di sini membuat seluruh deskew mati tanpa ada yang tahu.
        logger.warning(f"⚠️  Estimasi kemiringan gagal, deskew dilewati: {e}")

    return 0.0


def deskew_image(image: np.ndarray, angle: float | None = None) -> np.ndarray:
    """
    Merotasi citra untuk mengoreksi kemiringan dokumen.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image

    if angle is None:
        angle = estimate_skew_angle(gray)

    if abs(angle) < 0.3:
        return image  # Kemiringan diabaikan jika sangat kecil

    (h, w) = image.shape[:2]
    center = (w // 2, h // 2)
    rot_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

    # Hitung ukuran bounding baru agar tidak ada bagian dokumen yang terpotong
    cos = np.abs(rot_matrix[0, 0])
    sin = np.abs(rot_matrix[0, 1])
    new_w = int((h * sin) + (w * cos))
    new_h = int((h * cos) + (w * sin))

    rot_matrix[0, 2] += (new_w / 2) - center[0]
    rot_matrix[1, 2] += (new_h / 2) - center[1]

    # Putar dengan background putih
    deskewed = cv2.warpAffine(
        image,
        rot_matrix,
        (new_w, new_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255) if len(image.shape) == 3 else 255,
    )
    return deskewed


def enhance_contrast_and_lighting(image: np.ndarray) -> np.ndarray:
    """
    Menormalkan pencahayaan & meningkatkan kontras:
    1. Estimasi background dengan morphological closing untuk menghilangkan bayangan/kertas kusam
    2. Normalisasi pencahayaan (image / background)
    3. CLAHE (Contrast Limited Adaptive Histogram Equalization)
    """
    is_color = len(image.shape) == 3
    if is_color:
        # Konversi ke LAB color space untuk manipulasi Luminance (L-channel)
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l_channel, a_channel, b_channel = cv2.split(lab)
        gray = l_channel
    else:
        gray = image.copy()

    # 1. Background removal / Shadow equalization
    kernel_size = max(25, int(min(gray.shape) * 0.04))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))

    # Estimasi background
    background = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)

    # Division normalization: (gray / background) * 255
    normalized = np.float32(gray) / (np.float32(background) + 1e-5)
    normalized = np.clip(normalized * 255, 0, 255).astype(np.uint8)

    # 2. CLAHE pada kanal pencahayaan
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_l = clahe.apply(normalized)

    if is_color:
        enhanced_lab = cv2.merge((enhanced_l, a_channel, b_channel))
        return cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)
    else:
        return enhanced_l


def sharpen_and_denoise(image: np.ndarray) -> np.ndarray:
    """
    Menghilangkan noise halus dan menajamkan karakter teks / angka (Unsharp Masking).
    """
    # 1. Denoise lembut (Gaussian blur ringan)
    gaussian = cv2.GaussianBlur(image, (0, 0), 1.2)

    # 2. Unsharp masking formula: image * 1.5 - gaussian * 0.5
    sharpened = cv2.addWeighted(image, 1.5, gaussian, -0.5, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def preprocess_image_for_ocr(image: np.ndarray) -> np.ndarray:
    """
    Pipeline lengkap peningkatan kualitas citra untuk OCR: lurus -> kontras -> tajam.
    Input: numpy array (BGR/Gray). Output: numpy array BGR siap untuk PaddleOCR.
    """
    img_bgr = image.copy()
    if len(img_bgr.shape) == 2:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2BGR)

    # 1. Deskew. Langsung estimate_skew_angle: metrik kualitas lain tidak dipakai di sini
    # dan hanya menambah kerja di setiap halaman.
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    skew_angle = estimate_skew_angle(gray)
    if abs(skew_angle) > 0.5:
        logger.info(f"📐 Melakukan auto-deskew citra: {round(skew_angle, 2)}°")
        img_bgr = deskew_image(img_bgr, angle=skew_angle)

    # 2. Shadow removal & Contrast enhancement
    img_bgr = enhance_contrast_and_lighting(img_bgr)

    # 3. Sharpening & Denoise
    return sharpen_and_denoise(img_bgr)


def render_pdf_page_high_res(page: fitz.Page, target_dpi: int = 300) -> np.ndarray:
    """Merender halaman PDF ke citra BGR pada DPI tertentu (standar PDF = 72 DPI)."""
    zoom = target_dpi / 72.0
    return pixmap_to_bgr(page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False))


def pixmap_to_bgr(pix) -> np.ndarray:
    """
    Pixmap PyMuPDF -> array BGR OpenCV, TANPA merender ulang halaman.

    Dipisah dari render_pdf_page_high_res supaya pemanggil yang sudah punya pixmap
    (mis. untuk diambil PNG-nya) tidak perlu merender halaman yang sama dua kali.
    """
    img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 3:
        img_bgr = cv2.cvtColor(img_data, cv2.COLOR_RGB2BGR)
    elif pix.n == 1:
        img_bgr = cv2.cvtColor(img_data, cv2.COLOR_GRAY2BGR)
    elif pix.n == 4:
        img_bgr = cv2.cvtColor(img_data, cv2.COLOR_RGBA2BGR)
    else:
        img_bgr = img_data

    return img_bgr
