# dol-parser — image service (API + mesin OCR) untuk Linux/Windows-WSL2.
#
# Skema dol-schema (folder sejajar) masuk lewat konteks build bernama, supaya konteks
# utama tetap folder ini saja. Lewat docker compose sudah otomatis (additional_contexts).
#
#   CPU:
#     docker build -t openade:cpu --build-context dol-schema=../dol-schema .
#
#   GPU NVIDIA (PaddlePaddle tidak punya backend Metal, jadi hanya di sini ia memakai GPU).
#   Ambil nama image & perintah wheel yang TEPAT dari halaman instalasi resmi PaddlePaddle --
#   versi CUDA berubah-ubah, jadi sengaja tidak dipatok di sini:
#     docker build -t openade:gpu --build-context dol-schema=../dol-schema \
#       --build-arg BASE_IMAGE=nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04 \
#       --build-arg PADDLE_PKG=paddlepaddle-gpu \
#       --build-arg PADDLE_INDEX=https://www.paddlepaddle.org.cn/packages/stable/cu126/ .
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

ARG PADDLE_PKG=paddlepaddle
ARG PADDLE_INDEX=

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# libgl1 + libglib2.0-0 : dibutuhkan OpenCV (dipakai PaddleOCR & RapidOCR).
# libgomp1             : runtime OpenMP untuk onnxruntime/paddle.
# tesseract-ocr-ind    : Tesseract dipanggil sebagai BINER lewat CLI, bukan paket pip,
#                        jadi bahasanya harus dipasang di level sistem. Tanpa paket
#                        'ind' teks Indonesia berimbuhan banyak yang salah baca.
# python3/pip          : hanya terpakai bila BASE_IMAGE-nya bukan image python resmi
#                        (mis. base CUDA), makanya dipasang dengan '|| true' di bawah.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 libgomp1 \
        tesseract-ocr tesseract-ocr-ind tesseract-ocr-eng \
        ca-certificates \
    && (command -v python3 >/dev/null || apt-get install -y --no-install-recommends python3 python3-pip) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements disalin duluan supaya layer pip tidak ikut terbangun ulang tiap kali
# kode berubah -- instalasi torch/paddle di sini memakan waktu paling lama.
COPY requirements.txt .

# paddlepaddle dari requirements dilepas dulu, lalu dipasang sesuai varian yang diminta.
# Wheel CPU dan GPU tidak boleh terpasang bersamaan: yang belakangan menang diam-diam
# dan hasilnya sulit dilacak.
RUN sed -i '/^paddlepaddle/d' requirements.txt \
    && python3 -m pip install --upgrade pip \
    && python3 -m pip install -r requirements.txt \
    && if [ -n "$PADDLE_INDEX" ]; then \
           python3 -m pip install "$PADDLE_PKG" -i "$PADDLE_INDEX"; \
       else \
           python3 -m pip install "$PADDLE_PKG"; \
       fi

# Skema data bersama (dol-schema). Tanpa ini app/companion dan API gagal di-import.
COPY --from=dol-schema . /opt/dol-schema
RUN python3 -m pip install /opt/dol-schema

# sample_pdfs/ dikecualikan lewat .dockerignore (kontrak asli tidak ikut ke dalam
# image); di docker-compose.yml folder itu dipasang sebagai bind mount read-only.
COPY . .

# Model OCR (RapidOCR/Paddle) diunduh saat pertama dipakai. Folder-folder ini
# dijadikan volume di docker-compose.yml supaya unduhan itu tidak terulang tiap build.
ENV PADDLE_PDX_CACHE_HOME=/models/paddle \
    HF_HOME=/models/hf \
    OCR_ENGINE=rapidocr \
    LOG_LEVEL=INFO

# Default: cek mesin OCR apa saja yang hidup di image ini, bukan langsung menjalankan
# pipeline. Container yang mati tanpa pesan jelas lebih sulit didiagnosis daripada
# container yang bilang "engine X tidak tersedia".
CMD ["python3", "scripts/cek_ocr.py"]
