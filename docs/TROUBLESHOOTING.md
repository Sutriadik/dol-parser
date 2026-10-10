# Troubleshooting

Masalah yang **pernah terjadi** di proyek ini, disusun menurut gejala yang
terlihat. Cari pesan error Anda di halaman ini (Cmd+F / Ctrl+F); pesan ditulis persis
seperti yang muncul.

Semua perintah dijalankan dari folder `dol-parser/`.

---

## Cek cepat sebelum apa pun

Kebanyakan masalah berasal dari satu dari tiga layanan yang mati. Cek ketiganya dulu:

```bash
curl -s -o /dev/null -w "Ollama : %{http_code}\n" http://127.0.0.1:11434/api/tags
curl -s -o /dev/null -w "NocoDB : %{http_code}\n" http://localhost:8080/api/v1/health
docker info >/dev/null 2>&1 && echo "Docker : jalan" || echo "Docker : MATI"
```

Hasil sehat: `Ollama : 200`, `NocoDB : 200`, `Docker : jalan`. Angka `000` artinya layanan
tidak berjalan.

| Layanan | Menyalakan (macOS) |
|---|---|
| Ollama | `open -a Ollama` (atau `ollama serve`) |
| Docker | `open -a Docker`, tunggu ±30 detik sampai ikon paus berhenti bergerak |
| NocoDB | setelah Docker jalan: `docker start dol-nocodb` |

Ollama dan Docker **tidak menyala sendiri** setelah laptop restart atau tidur lama. Data
NocoDB aman: tersimpan di volume Docker, tidak hilang saat container mati.

---

## 1. Ollama (LLM)

### `Failed to connect to Ollama. Please check that Ollama is downloaded, running and accessible.`

Muncul sebagai `ExtractionError: Extraction gagal untuk tipe 'contract': Failed to connect to Ollama...`.
Kadang juga dalam versi Indonesia: `Tidak bisa terhubung ke Ollama di http://127.0.0.1:11434`.

- **Penyebab:** aplikasi Ollama tidak berjalan.
- **Perbaikan:** `open -a Ollama`, lalu cek `curl http://127.0.0.1:11434/api/tags`.
  Dokumen yang gagal harus diproses ulang; tidak ada yang tersimpan setengah jalan.
- **Tanda:** parsing (OCR) tetap berjalan normal beberapa menit, baru gagal di tahap ekstraksi.

> Sejak 30 Sep 2026 yang tampil adalah pesan Indonesia. Pesan Inggris di atas masih bisa
> muncul di log lama; keduanya berarti hal yang sama.

### `Ollama tidak merespons dalam 600s (… token prompt).`

- **Penyebab:** Ollama berjalan tetapi terlalu lambat: biasanya RAM penuh (lihat bagian 5).
- **Perbaikan:** tutup aplikasi berat (browser dengan banyak tab, Docker bila tidak dipakai),
  lalu proses ulang. Bila tetap terjadi, naikkan `OLLAMA_TIMEOUT` di `.env`.

### Model tidak ditemukan

`GET /health` menunjukkan `"target_model_installed": false`.

- **Perbaikan:** `ollama pull qwen2.5:7b` (atau model yang tertulis di `OLLAMA_MODEL`).

---

## 2. NocoDB

### NocoDB tidak bisa dibuka / `Connection refused` / `[Errno 61]`

- **Penyebab:** Docker Desktop atau container NocoDB mati.
- **Perbaikan:** lihat tabel "Cek cepat" di atas: `open -a Docker`, lalu `docker start dol-nocodb`.

### `failed to connect to the docker API at unix:///…/docker.sock`

- **Penyebab:** Docker Desktop belum berjalan.
- **Perbaikan:** `open -a Docker`, tunggu sampai `docker info` tidak lagi error.

### Push "berhasil" tetapi tidak ada data masuk / `Tabel '…' dilewati: tidak ada di NOCODB_TABLE_IDS`

- **Penyebab:** `NOCODB_TABLE_IDS` di `.env` menunjuk ke tabel yang namanya tidak sama dengan
  tabel yang dikirim pipeline. Pernah terjadi September 2026: `.env` menunjuk ke 5 tabel ERD
  lama, pipeline mengirim tabel `dol_schema`, jadi nol baris masuk.
- **Perbaikan:**
  ```bash
  set -a; . ./.env; set +a
  .venv311/bin/python scripts/nocodb_setup.py --check            # tableId valid?
  .venv311/bin/python scripts/nocodb_setup.py --compare-schema   # kolom sama dengan dol_schema?
  ```
  Bila tabel belum ada: `scripts/nocodb_setup.py --create-base` membuat base "DOL Schema"
  lengkap dan mencetak baris `NOCODB_TABLE_IDS=` untuk disalin ke `.env`.

### `Tabel '…' belum ada di NOCODB_TABLE_IDS. Jalankan: python nocodb_setup.py --list-tables`

- Sama seperti di atas. Tabel yang dimaksud ada di `dol_schema` tetapi tidak dipetakan di `.env`.

### `push_to_nocodb diminta tapi NOCODB_PUSH_ENABLED=false.` (HTTP 409)

- **Penyebab:** push dari API sengaja dimatikan secara default; pada arsitektur briefing,
  n8n yang mengirim ke NocoDB.
- **Perbaikan:** bila memang ingin FastAPI mengirim langsung, isi `NOCODB_PUSH_ENABLED=1`
  di `.env` lalu jalankan ulang server.

### `Payload tidak lolos validasi, tidak dikirim.`

- **Penyebab:** ada baris yang tidak sesuai `dol_schema` (kolom wajib kosong, pilihan nilai
  di luar daftar, dsb). Pusher menolak mengirim sebagian-sebagian.
- **Perbaikan:** baca 10 masalah pertama yang dicetak di bawah pesan itu. Untuk berkas
  companion: `scripts/companion.py --check <berkas>.companion.json`.

### `'field_review' ditulis manusia; pipeline tidak boleh mengirim ke sini.`

- Disengaja. `field_review` hanya diisi PM. Jangan mengirim tabel ini dari pipeline.

### Nilai uang tampil dengan simbol dolar ($)

- **Penyebab:** kolom `Currency` NocoDB memakai bawaan USD bila tidak diatur.
- **Status:** sudah diperbaiki di base "DOL Schema" (29 Sep 2026) dan `--create-base`
  otomatis memakai Rupiah (`id-ID`, `IDR`). Bila muncul lagi di tabel lain: di NocoDB, klik
  judul kolom → *Edit* → *Currency* → pilih Rupiah.

### `evidence_score` tampil "0,876%" padahal maksudnya 87,6%

- **Status:** diperbaiki di skema `companion-2026.10.1` (kolom skor kini `Decimal`). Hanya
  terjadi di base lama "DOL Schema"; base "DOL Schema 2026.10.1" sudah benar. Di base lama,
  baca angkanya dikali 100.

---

### `'…' sudah punya N keputusan PM. Proses ulang tidak menimpa dokumen yang sedang/sudah diperiksa`

- **Penyebab:** disengaja. Dokumen ini sudah punya baris di tabel Keputusan PM; memproses
  ulang akan menggeser nilai dan nomor baris di bawah keputusan yang sudah dibuat.
- **Perbaikan:** bila PM memang meminta dokumen diproses ulang:
  `scripts/companion.py --push <berkas>.companion.json --timpa-yang-direview`. Keputusan
  yang nilainya berubah akan muncul sebagai basi (view `field_review_stale`) dan harus
  diputuskan ulang.

### `companion_payload` bernilai `null` untuk dokumen BAST

- **Penyebab:** disengaja. Tabel BAST masih usulan di dol-schema (status `ditunda`);
  alasannya tertulis di `companion_catatan`. Hasil ekstraksinya tetap ada di
  `storage/outputs/extraction/*.extract.json`.

### `tidak ada berkas asli maupun run_info.document_id; identitas dokumen tidak bisa ditentukan`

- **Penyebab:** `*.extract.json` lama (sebelum kunci dokumen diseragamkan) tanpa
  `run_info.document_id`, dan PDF aslinya tidak ditemukan.
- **Perbaikan:** sertakan berkasnya: `scripts/companion.py --map <berkas> --pdf <pdf asli>`.

## 3. Lingkungan Python & editor

### `No module named 'paddle'` / `No module named 'dol_schema'` / versi Python salah

- **Penyebab:** perintah `python3` di Mac ini menunjuk ke Python 3.14 sistem, bukan
  lingkungan proyek. Paddle belum mendukung 3.14.
- **Perbaikan:** selalu pakai `.venv311/bin/python`, bukan `python3`.

### `bad interpreter: …/buildAParser/.venv311/bin/python3.11` saat menjalankan `pytest`

- **Penyebab:** skrip di `.venv311/bin/` (pytest, uvicorn, `activate`, dll.) menyimpan path
  lengkap folder saat venv dibuat. Folder proyek pernah bernama `buildAParser`; setelah
  di-rename, 99 skrip menunjuk ke folder yang sudah tidak ada. **Sudah diperbaiki 30 Sep 2026.**
- **Bila terjadi lagi** (folder proyek dipindah atau di-rename): ganti path lama dengan yang baru
  di skrip `bin/`, tanpa memasang ulang paket:
  ```bash
  cd .venv311
  grep -l "<PATH_LAMA>" bin/* pyvenv.cfg | xargs sed -i '' "s|<PATH_LAMA>|$(cd .. && pwd)|g"
  ```
  Sementara itu, panggil lewat modul selalu berhasil: `.venv311/bin/python -m pytest`.

### Skrip CLI membaca `NOCODB_API_TOKEN kosong` padahal `.env` sudah terisi

- **Penyebab:** `app/config.py` membaca environment variable, tetapi **tidak** memuat berkas
  `.env` sendiri. Server lewat Docker Compose memuatnya (`env_file`), skrip CLI tidak.
- **Perbaikan:** muat dulu di terminal yang sama:
  ```bash
  set -a; . ./.env; set +a
  ```

### Editor (VS Code / Antigravity) penuh garis merah `Cannot find module …`

- **Penyebab:** editor memakai interpreter Python yang salah (3.14 sistem). Kodenya sendiri
  tidak rusak; `make test` tetap lulus.
- **Perbaikan:** `Cmd+Shift+P` → *Python: Select Interpreter* → pilih
  `dol-parser/.venv311/bin/python`, lalu *Developer: Reload Window*.

### `ModuleNotFoundError: No module named 'dol_schema'` di dalam `.venv311`

- **Penyebab:** paket `dol-schema` belum terpasang, atau folder `dol-schema` tidak
  bersebelahan dengan `dol-parser`.
- **Perbaikan:** `.venv311/bin/pip install -e ../dol-schema`

---

## 4. Hasil ekstraksi

### `Teks hasil parsing terlalu sedikit (… karakter untuk … halaman, …`

- **Penyebab:** hasil OCR (hampir) kosong: biasanya berkas hasil foto buram atau halaman
  terbalik. Sistem sengaja berhenti daripada mengirim teks kosong ke LLM (hasilnya pasti
  karangan).
- **Perbaikan:** coba mesin OCR lain: `ocr=mac` (Apple Vision, macOS saja) atau
  `ocr=tesseract`. `make cek-ocr` menampilkan mesin yang tersedia.

### `Docling gagal membaca … (ocr=…): …. Mesin lain tidak dicoba otomatis`

- **Penyebab:** mesin OCR yang diminta tidak bisa dipakai (nama salah ketik, paket atau
  biner belum terpasang), atau Docling gagal membuka berkasnya. Bagian setelah titik dua
  adalah galat aslinya.
- **Perbaikan:** betulkan nilai `OCR_ENGINE` / `ocr=`, atau minta mesin lain terang-terangan,
  mis. `OCR_ENGINE=paddle .venv311/bin/python run.py "dokumen.pdf"`. Sistem sengaja tidak
  beralih mesin sendiri: hasil dari mesin yang tidak diminta sulit dilacak.

### Validasi `fail` padahal nilainya terlihat benar

- Periksa dulu dokumen aslinya. Contoh nyata (angka disamarkan): SPH PT Vendor A menulis
  subtotal 519,6 jt, PPN 52,44 jt, dan total 533,2 jt: **dokumen vendornya sendiri tidak
  konsisten**. Sistem benar menandainya `CONFLICT`; keputusan ada di PM.

### `Berkas berisi salinan ganda: halaman X-Y mengulang halaman sebelumnya dan tidak ikut diekstrak.`

- **Artinya:** PDF berisi dua salinan dokumen yang sama (umum pada kontrak "half signed"
  yang dipindai untuk tiap pihak). Hanya salinan pertama yang dibaca AI; tanpa ini setiap
  item terhitung dua kali. Contoh: sebuah kontrak 16 halaman = 8 × 2.
- **Yang perlu dilakukan PM:** buka halaman yang disebut dan pastikan isinya memang sama.
  Bila salinan kedua ternyata **revisi** (harga atau tanggal berbeda), proses ulang dengan
  PDF yang hanya berisi versi terbaru.

### `Invalid JSON: EOF while parsing a string` / nilai berisi `0123456789012345…`

- **Penyebab:** LLM terjebak mengulang deret angka di satu field sampai kuota token habis,
  sehingga JSON terpotong. Contoh nyata: kontrak pindaian 16 halaman, alamat Pihak
  Pertama diikuti `0812345678901234567890…`; dokumen 16 halaman gagal total.
- **Status:** diperbaiki 30 Sep 2026 dengan tiga lapis: setiap field teks diberi batas
  panjang di skema yang dikirim ke Ollama; deret angka ≥ 20 digit tanpa pemisah dibuang
  dari nilai; dan bila JSON pass 1 tetap rusak, ekstraksi lanjut lewat retry per field
  alih-alih menggagalkan dokumen.
- Bila pesan ini muncul lagi, laporkan nama berkasnya.

### Angka uang raksasa atau digit yang tersambung

- **Penyebab (sudah diperbaiki 29 Sep 2026):** OCR memecah angka seperti `52,437,916` menjadi
  potongan `52,43` / `7` / `,916`, lalu LLM menyambungnya dengan angka lain.
- Bila muncul lagi pada dokumen baru, laporkan dengan nama berkasnya; kemungkinan tata
  letak yang belum dikenali `app/parsers/block_grouper.py`.

### `Sanity guard: PPN/Grand Total absurd … dikosongkan untuk ditinjau PM`

- Disengaja. PPN lebih besar dari subtotal, atau total lebih dari 2× subtotal, pasti salah
  baca. Sistem mengosongkannya (**tidak** menghitung ulang) supaya PM mengisinya dari dokumen.

---

## 5. Kinerja

### Satu dokumen butuh lebih dari 5 menit

Normal di MacBook Air M4 dengan RapidOCR: 1,5–6 menit per dokumen, sebagian besar untuk LLM.

| Penyebab | Perbaikan |
|---|---|
| Mesin OCR `paddle` | jangan dipakai di Mac: ~30 detik/halaman, RAM hingga 11 GB. Pakai `rapidocr` (default) atau `mac` |
| RAM penuh (Ollama ±5,5 GB + Docker + browser) | tutup aplikasi lain; memori 16 GB cepat habis |
| Ollama baru dinyalakan | panggilan pertama memuat model; dokumen kedua lebih cepat |

Jangan aktifkan CoreML untuk RapidOCR; sudah diuji 5–8× **lebih lambat**.

### n8n timeout saat memanggil `/api/v1/process-all`

- **Penyebab:** satu dokumen butuh 160–310 detik, lebih lama dari timeout HTTP n8n.
- **Perbaikan:** pakai jalur asinkron `POST /api/v1/jobs` + `callback_url` atau polling.
  Lihat [INTEGRASI_N8N.md](INTEGRASI_N8N.md).

---

## 6. Skema (dol-schema)

### `Artefak tidak sesuai model: …` saat `python -m dol_schema --check`

- **Penyebab:** `model.py` diubah tetapi `generated/` belum dibangkitkan ulang, atau
  sebaliknya, berkas di `generated/` diedit tangan.
- **Perbaikan:** `python -m dol_schema --emit`, lalu commit `model.py` dan `generated/`
  bersamaan. Jangan pernah mengedit `generated/` langsung.

### Tabel NocoDB tidak cocok dengan diagram

```bash
set -a; . ./.env; set +a
.venv311/bin/python scripts/nocodb_setup.py --compare-schema
```

Perintah ini menyebut kolom yang hilang, berlebih, atau berbeda tipe per tabel.

---

## 7. Git

### Setelah `git pull` riwayat jadi dobel / muncul commit lama

- **Penyebab:** riwayat cabang `refactor/audit-2026-09` pernah ditulis ulang (29 Sep 2026,
  menghapus atribusi otomatis di pesan commit). Salinan di laptop lain masih memegang riwayat lama.
- **Perbaikan** (pastikan tidak ada perubahan yang belum di-commit di laptop itu):
  ```bash
  git fetch
  git reset --hard origin/refactor/audit-2026-09
  ```

---

Menemukan masalah baru? Tambahkan di bagian yang sesuai: **gejala** (pesan persis),
**penyebab**, **perbaikan**. Satu masalah, satu entri.
