# Menguji PaddleOCR di Windows lewat Docker

## Kesimpulan lebih dulu

Docker adalah pilihan yang tepat, **tapi satu hal harus dipastikan sebelum repot
menyiapkan apa pun: apakah PC Windows itu punya GPU NVIDIA?** Jawabannya menentukan
apakah pengujian ini layak dikerjakan sama sekali.

| Kondisi PC Windows | Perkiraan hasil | Layak? |
|---|---|---|
| **Ada GPU NVIDIA** | PaddleOCR bisa dipercepat berkali-kali lipat | **Ya, ini inti pengujiannya** |
| Tanpa GPU NVIDIA (Intel/AMD/iGPU) | Sedikit lebih cepat dari Mac, tetap lambat | Kurang layak |

Alasannya sudah terbukti di mesin ini. Jalankan `python cek_ocr.py` di Mac:

```
✅ paddle    v3.3.1 CPU saja (wheel non-GPU)
```

PaddlePaddle **tidak punya backend Metal**, jadi di Apple Silicon ia selalu berjalan di
CPU. Itu sebabnya parse-nya 393 detik rata-rata, sementara RapidOCR 26 detik. Pindah ke
Windows hanya menyelesaikan masalah itu **kalau di sana ada CUDA**. Kalau PC-nya tanpa
GPU NVIDIA, yang Anda ukur cuma "CPU x86 vs CPU ARM" — beda, tapi bukan beda yang sedang
dicari.

## Satu hal yang sering disalahpahami

Docker Desktop di Windows menjalankan container **Linux** di atas WSL2. Jadi yang Anda uji
sebenarnya "PaddleOCR di Linux/WSL2", bukan "PaddleOCR di Windows". Itu justru bagus —
produksi nanti kemungkinan besar Linux — tapi jangan sampai salah menyimpulkan.

Konsekuensi lain: **Apple Vision tidak akan ada di container**, karena itu API macOS. Di
Linux tersisa 3 mesin: RapidOCR, Tesseract, PaddleOCR. (EasyOCR dibuang 2026-10-07.)

## Strategi: uji tahap parse saja dulu, jangan seluruh pipeline

Ini saran yang paling menghemat waktu Anda.

Pertanyaan yang ingin dijawab adalah **kecepatan dan hasil OCR**. Untuk itu Anda tidak
perlu LLM-nya sama sekali. Di `app/services/engine.py` extractor dimuat lazy, jadi
`parse()` tidak pernah menyentuh Ollama. Artinya:

- container cukup berisi parser + mesin OCR (tanpa Ollama, tanpa model 4,7 GB),
- waktu build jauh lebih pendek,
- angka waktunya bersih — tidak tercampur waktu LLM yang tidak ada hubungannya dengan OCR.

Baru kalau hasil parse-nya memuaskan, Ollama ditambahkan belakangan.

## Langkah

### 1. Prasyarat di Windows

- Windows 10/11 + **WSL2** aktif
- **Docker Desktop** dengan backend WSL2
- Kalau ber-GPU: driver NVIDIA versi terbaru **di Windows** (bukan di dalam WSL), lalu
  pastikan `wsl --update` sudah dijalankan. NVIDIA Container Toolkit sudah otomatis
  tersedia lewat Docker Desktop.

Uji GPU-nya tembus ke container dulu, sebelum menyentuh repo ini:

```powershell
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

Kalau perintah itu tidak menampilkan tabel GPU, hentikan di sini — semua langkah
berikutnya akan sia-sia.

### 2. Taruh repo di dalam filesystem WSL2, bukan di C:\

Ini bukan detail sepele. Bind mount dari `C:\...` ke container menembus lapisan
penerjemah filesystem Windows→Linux dan bisa membuat pembacaan berkas berkali-kali lipat
lebih lambat — cukup untuk mengacaukan pengukuran waktu yang sedang Anda kerjakan.

```bash
# di dalam WSL2 (Ubuntu)
cd ~
git clone <repo-anda> buildAParser
cd buildAParser
```

### 3. Build

```bash
# CPU — untuk memastikan semuanya jalan dulu
docker compose build ocr-cpu

# GPU — sesuaikan versi CUDA-nya dengan driver Anda.
# Ambil perintah wheel yang TEPAT dari halaman instalasi resmi PaddlePaddle;
# nilai di docker-compose.yml hanya contoh dan berubah-ubah antar rilis.
docker compose build ocr-gpu
```

### 4. Pastikan mesinnya hidup sebelum mengukur apa pun

```bash
docker compose run --rm ocr-gpu python3 cek_ocr.py
```

Yang dicari satu baris ini:

```
✅ paddle    v3.x.x CUDA aktif — 1x NVIDIA GeForce RTX ...
```

Kalau yang muncul `wheel GPU terpasang TAPI tidak ada GPU terdeteksi`, berarti
passthrough-nya belum jalan dan angka yang keluar nanti tetap angka CPU — persis jebakan
yang membuat orang menyimpulkan "ternyata GPU tidak membantu".

### 5. Ukur

```bash
# parse 2x, cetak waktu + hash teks. Tidak butuh Ollama.
docker compose run --rm ocr-gpu python3 bench_ocr.py --consistency paddle --doc spk
docker compose run --rm ocr-gpu python3 bench_ocr.py --consistency paddle --doc kl
```

Bandingkan dengan angka Mac ini (dokumen yang sama persis):

| Dokumen | Paddle di Mac (CPU) | RapidOCR di Mac | Target Windows+GPU |
|---|---|---|---|
| SPK (3 hlm) | 223s / 122s | 14s / 11s | < 30s |
| KL (14 hlm) | 448s | 44s | < 60s |

### 6. Hal yang paling berharga dari pengujian ini

`--consistency` mencetak **sha1 teks hasil OCR**. Di Mac, PaddleOCR pada SPK menghasilkan:

```
sha1=299e7ee28b11  6360 char
```

Kalau di Windows hash-nya **sama persis**, Anda punya bukti kuat bahwa hasil OCR tidak
berubah antar-platform — hanya kecepatannya. Kalau **berbeda**, seluruh angka akurasi yang
sudah diukur di Mac tidak otomatis berlaku di Linux, dan `skor_ocr.py` harus dijalankan
ulang di sana. Itu temuan yang jauh lebih penting daripada sekadar catatan waktu.

## Kalau ternyata tidak ada GPU NVIDIA

Jangan pakai Docker untuk ini. Lebih cepat dan cukup:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python cek_ocr.py
.venv\Scripts\python bench_ocr.py --consistency paddle --doc spk
```

`requirements.txt` sudah memakai penanda platform, jadi `ocrmac` (khusus macOS) otomatis
dilewati di Windows dan instalasinya tidak gagal.

Satu hal yang tetap perlu dipasang manual di Windows kalau mau menguji Tesseract: biner
`tesseract` beserta bahasa `ind`. Docling memanggilnya lewat CLI, bukan lewat paket pip,
jadi `pip install` saja tidak cukup.

## Jujur soal batas dokumen ini

Berkas `Dockerfile` dan `docker-compose.yml` di repo ini **belum pernah saya build dan
jalankan** — daemon Docker di Mac ini tidak aktif, dan build image ber-CUDA tidak mungkin
diuji dari macOS ARM. Jadi anggap keduanya titik awal yang masuk akal, bukan sesuatu yang
sudah terbukti jalan. Kesalahan yang paling mungkin muncul pada build pertama: nama paket
sistem yang berbeda antar rilis Debian/Ubuntu, dan versi CUDA yang tidak cocok dengan
driver. Keduanya ketahuan langsung saat build, bukan diam-diam.

---

# Daftar langkah: di Mac dulu, baru di Windows

## Bagian A — di MacBook (sekali saja)

### A1. Baseline sudah disimpan ✅

`storage/outputs/bench/_hasil/_baseline_macos.json` berisi 15 kombinasi
(5 mesin x 3 dokumen) lengkap dengan **sha1 teks OCR**. Ini pembanding wajibnya.
Yang paling penting dari berkas itu:

| Kombinasi | sha1 teks OCR | Karakter | Parse |
|---|---|---|---|
| `spk__paddle` | `299e7ee28b11` | 6.360 | 335,2s |
| `kl__paddle` | `e936ff20e0a8` | 43.462 | 448,2s |
| `np__paddle` | `ddba0d520804` | 6.118 | 396,0s |

### A2. Pindahkan repo ke laptop Windows

Kode lewat git (repo privat GitHub), data klien lewat `buildAParser-data.zip` di luar
git. Langkah lengkapnya ada di [JALANKAN_DI_WINDOWS.md](JALANKAN_DI_WINDOWS.md) — di
Docker/WSL2, ganti `C:\dev\buildAParser` di sana dengan `~/buildAParser` di dalam WSL.

## Bagian B — di laptop Windows (Lenovo LOQ)

LOQ itu lini gaming, jadi kemungkinan besar ada RTX di dalamnya. Tapi **buktikan
dulu**, jangan diasumsikan.

### B1. Pastikan GPU-nya ada dan terbaca

Buka PowerShell:

```powershell
nvidia-smi
```

Harus muncul tabel berisi nama GPU dan versi driver. Kalau `nvidia-smi` tidak
dikenali, pasang dulu driver NVIDIA terbaru dari situs resmi (bukan lewat Windows
Update). **Catat berapa VRAM-nya** — angka itu dipakai di langkah B6.

### B2. Pasang WSL2 dan Docker Desktop

```powershell
wsl --install
wsl --update
```

Lalu pasang Docker Desktop, dan di Settings pastikan **"Use the WSL 2 based engine"**
aktif.

### B3. Uji GPU tembus ke container — JANGAN dilewati

```powershell
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

Kalau tabel GPU tidak muncul di sini, berhenti. Semua langkah setelah ini akan
menghasilkan angka CPU, dan Anda akan salah menyimpulkan "ternyata GPU tidak membantu".

### B4. Taruh repo di dalam WSL2, bukan di C:\

```bash
wsl                       # masuk ke Ubuntu
cd ~
git clone https://github.com/Sutriadik/BuildAparser.git buildAParser
cd buildAParser
unzip /mnt/c/Users/<nama-anda>/Downloads/buildAParser-data.zip   # data klien
```

Bind mount dari `C:\` menembus penerjemah filesystem Windows→Linux dan jauh lebih
lambat — cukup untuk merusak pengukuran waktu yang justru sedang dicari.

### B5. Build

```bash
docker compose build ocr-cpu        # pastikan jalan dulu
docker compose build ocr-gpu        # sesuaikan versi CUDA di docker-compose.yml
```

### B6. Periksa CUDA benar-benar aktif

```bash
docker compose run --rm ocr-gpu python3 cek_ocr.py
```

Yang dicari satu baris ini:

```
✅ paddle    v3.x.x CUDA aktif — 1x NVIDIA GeForce RTX ...
```

Kalau muncul `wheel GPU terpasang TAPI tidak ada GPU terdeteksi`, passthrough-nya
belum jalan — kembali ke B3.

### B7. Ukur dan bandingkan

```bash
docker compose run --rm ocr-gpu python3 bench_ocr.py --consistency paddle --doc spk
docker compose run --rm ocr-gpu python3 bench_ocr.py --consistency paddle --doc kl
```

Dua hal yang dibandingkan, dan yang kedua lebih penting:

1. **Waktu** — Mac: SPK 335s, KL 448s. Target dengan GPU: di bawah 60s.
2. **sha1** — Mac: SPK `299e7ee28b11`, KL `e936ff20e0a8`.
   - **Sama** → teks OCR identik lintas platform; seluruh angka akurasi hasil
     pengukuran di Mac tetap berlaku di Linux.
   - **Beda** → angka akurasi Mac tidak otomatis berlaku, dan `skor_ocr.py` harus
     dijalankan ulang di Windows. Ini temuan yang jauh lebih berarti daripada catatan waktu.

### B8 (opsional tapi menarik) — uji seluruh pipeline, bukan cuma OCR

Ini di luar pertanyaan awal, tapi sayang dilewatkan. Dari pengukuran di Mac,
**OCR hanya 9–12% dari total waktu; sisanya LLM.** Artinya laptop ber-RTX berpotensi
mempercepat seluruh pipeline jauh lebih banyak daripada sekadar mempercepat Paddle.

Syaratnya model harus muat di VRAM: `qwen2.5:7b` kuantisasi Q4 butuh sekitar 4,7 GB.
RTX dengan 6 GB ke atas muat; kalau hanya 4 GB, sebagian jatuh ke CPU dan justru
bisa lebih lambat — karena itu VRAM-nya dicatat di langkah B1.

```powershell
# Ollama punya installer Windows dengan dukungan CUDA
ollama pull qwen2.5:7b
ollama run qwen2.5:7b "halo"      # cek kecepatannya
```

Kalau muat, jalankan `python bench_ocr.py --all` di sana dan bandingkan kolom
`extract_s` dengan angka Mac (KL: 267,7s dengan RapidOCR).
