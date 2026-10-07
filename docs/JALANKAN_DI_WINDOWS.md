# Menjalankan Open ADE di Windows (venv, tanpa Docker)

Kode diambil lewat git. **Data klien tidak ada di git**: kontrak di `sample_pdfs/` dan
semua turunannya (teks OCR, hasil ekstraksi, golden set) dipindahkan terpisah lewat
`buildAParser-data.zip`. Alasannya: repo ada di GitHub, dan dokumen itu milik klien.

Semua perintah di bawah diketik di **PowerShell**.

## 0. Pasang sekali saja

| Apa | Dari mana | Catatan |
|---|---|---|
| Git for Windows | git-scm.com | Biarkan pilihan default saat instalasi |
| Python **3.11** | python.org | Centang **"Add python.exe to PATH"**. Samakan dengan Mac (3.11.16) |
| Ollama | ollama.com/download | Baru dibutuhkan untuk tahap ekstraksi (langkah 6) |

Cek hasilnya:

```powershell
git --version
py -3.11 --version
```

## 1. Aktifkan path panjang

Beberapa berkas di `sample_pdfs/` panjang path-nya lebih dari 230 karakter, sedangkan
batas bawaan Windows 260. Tanpa langkah ini, ekstraksi zip gagal di tengah jalan.

PowerShell **sebagai Administrator** (klik kanan → Run as administrator):

```powershell
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name "LongPathsEnabled" -Value 1 -PropertyType DWORD -Force
```

Lalu, di PowerShell biasa:

```powershell
git config --global core.longpaths true
git config --global user.name  "Nama Kamu"
git config --global user.email "email-kamu@example.com"
```

Taruh repo di path pendek (`C:\dev`), bukan di `C:\Users\<nama>\Documents\...`.

## 2. Ambil kode

```powershell
mkdir C:\dev
cd C:\dev
git clone https://github.com/Sutriadik/BuildAparser.git buildAParser
cd buildAParser
git switch refactor/audit-2026-09
```

Repo-nya privat, jadi saat `clone` akan muncul jendela login GitHub (Git Credential
Manager). Login sekali; setelah itu `git pull` tidak akan bertanya lagi.

## 3. Masukkan data klien

Salin `buildAParser-data.zip` dari Mac (flashdisk / Google Drive) ke `Downloads`, lalu:

```powershell
tar -xf $HOME\Downloads\buildAParser-data.zip -C C:\dev\buildAParser
git status
```

`git status` harus menjawab **"nothing to commit, working tree clean"**. Kalau muncul
daftar berkas PDF atau `storage/outputs`, berarti `.gitignore`-nya tidak terbaca. Jangan
commit apa pun; tanyakan dulu.

Nama folder di dalam zip sudah disesuaikan untuk Windows: karakter `:` diganti `-` dan
spasi di ujung nama folder dibuang (ada 12 path yang terdampak). Dokumen uji utama
di `sample_pdfs/` tidak terdampak.

## 4. Siapkan Python

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

Kalau muncul error *"running scripts is disabled on this system"*, jalankan sekali:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, lalu ulangi `Activate.ps1`.
Prompt akan diawali `(.venv)` kalau sudah aktif.

**Wajib:** mode UTF-8. Tanpa ini, emoji di output (✅ 🚀) dan pembacaan JSON berbahasa
Indonesia gagal dengan `UnicodeEncodeError` / `UnicodeDecodeError`, karena Windows
default-nya memakai cp1252.

```powershell
setx PYTHONUTF8 1          # permanen, berlaku untuk jendela PowerShell BERIKUTNYA
$env:PYTHONUTF8 = "1"      # untuk jendela yang sedang terbuka
```

Pasang dependensi. Ini lama (15–30 menit), karena model layout Docling menarik PyTorch sekitar 2 GB:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e ..\dol-schema     # skema data bersama: clone repo dol-schema SEJAJAR folder ini
```

`ocrmac` (khusus macOS) otomatis dilewati berkat penanda platform di `requirements.txt`.
Tanpa `dol-schema`, API gagal dijalankan dengan `ModuleNotFoundError: dol_schema`.

## 5. Uji tahap parse (tanpa LLM)

```powershell
python cek_ocr.py
```

Harus muncul `✅` untuk `rapidocr`. `mac` (Apple Vision) memang tidak ada di Windows.

Lalu ukur dan bandingkan dengan Mac:

```powershell
python bench_ocr.py --consistency rapidocr --doc spk
python bench_ocr.py --consistency rapidocr --doc kl
```

| Dokumen | sha1 teks OCR di Mac | Karakter | Parse di Mac |
|---|---|---|---|
| SPK | `6fc196886c73` | 5.866 | 21,6s |
| KL | `b0c1357d862f` | 28.810 | 44,5s |
| NP | `f4f1016cf595` | 5.855 | 13,4s |

Yang paling penting adalah **sha1**, bukan waktunya. Kalau sha1-nya sama, teks OCR identik
lintas platform, dan skor akurasi yang diukur di Mac juga berlaku di Windows. Kalau
berbeda, `skor_ocr.py` harus dijalankan ulang di Windows.

## 6. Pipeline lengkap (parse + ekstraksi LLM)

Setelah Ollama terpasang (ikon llama di system tray berarti server-nya sudah jalan):

```powershell
ollama pull qwen2.5:7b
python run.py "sample_pdfs\nama_dokumen.pdf"
```

Hasilnya masuk ke `storage\outputs\` dan tidak ikut git.

Kalau laptopnya punya GPU NVIDIA, Ollama di Windows otomatis memakai CUDA. Cek dengan
`ollama ps` saat model sedang jalan: kolom `PROCESSOR` harus berisi `100% GPU`.

## 7. Alur kerja sehari-hari

```
Mac:      edit kode → git add … → git commit → git push
Windows:  git pull  → jalankan
```

Arah sebaliknya juga bisa (commit di Windows, `git pull` di Mac).

Yang **tidak** lewat git: PDF, isi `storage/outputs/`, dan `eval/golden|baseline_v1|reports`.
Kalau ingin membawa hasil benchmark Windows ke Mac, salin manual berkas
`storage\outputs\bench\_hasil\*.json`. Berkas itu hanya berisi angka dan hash, tanpa
teks kontrak.

## Masalah yang mungkin muncul

| Gejala | Penyebab | Solusi |
|---|---|---|
| `UnicodeEncodeError: 'charmap'` | Mode UTF-8 belum aktif | Langkah 4, `$env:PYTHONUTF8 = "1"` |
| `Filename too long` saat clone/unzip | Path panjang belum aktif | Langkah 1 |
| `py` tidak dikenali | Python belum di PATH | Pasang ulang, centang "Add to PATH" |
| `pip` gagal di `paddlepaddle` | Tidak ada wheel untuk versi Python itu | Pastikan venv dibuat dengan `py -3.11` |
| Ekstraksi gagal `Connection refused :11434` | Ollama tidak jalan | Buka aplikasi Ollama dari Start menu |
