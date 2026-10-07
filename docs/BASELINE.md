# Baseline — keadaan sebelum apa pun diubah

> **Status: BELUM DIISI.** Ini rangka; angkanya harus dikumpulkan dari orang, bukan dari kode.

Briefing hlm. 20 menyebut ini aturan yang tidak bisa ditawar: *"Baseline diukur sebelum apa
pun diubah. Tanpa angka awal, tidak ada cara membuktikan proyek ini berhasil."* Dan hlm. 21
menjadikannya 1 dari 4 kriteria penilaian (**Terukur**).

Isi dengan angka, bukan kesan. "Lama" bukan baseline; "rata-rata 3,5 hari dari 12 proyek
terakhir" baru baseline.

---

## 1. Rantai hilir — dari barang terpasang sampai uang masuk

Ini yang paling penting: briefing hlm. 4 menyebut BAST sebagai dokumen yang menahan cashflow.

| Yang diukur | Angka | Dari mana | Tanggal ukur |
|---|---|---|---|
| Barang terpasang → BAST Pelanggan diteken | ___ hari | | |
| BAST diteken → invoice terbit | ___ hari | | |
| Invoice terbit → uang masuk | ___ hari | | |
| **Total: terpasang → uang masuk** | **___ hari** | | |

Ambil dari **minimal 10 proyek terakhir yang sudah tutup**. Catat juga sebaran, bukan cuma
rata-rata — satu proyek 40 hari menutupi sembilan proyek 5 hari.

| Proyek | Terpasang | BAST diteken | Invoice | Uang masuk | Total hari |
|---|---|---|---|---|---|
| | | | | | |

---

## 2. BAST Pelanggan yang pernah ditolak

Deliverable Bulan 1 Network Engineer (hlm. 15), tapi hasilnya dipakai bertiga.

| Yang diukur | Angka |
|---|---|
| Jumlah BAST Pelanggan 12 bulan terakhir | ___ |
| Berapa yang dikembalikan/ditolak minimal sekali | ___ (___%) |
| Rata-rata berapa kali bolak-balik sebelum diterima | ___ |
| Tambahan hari akibat satu kali penolakan | ___ hari |

**Alasan penolakan — ini yang menentukan apa yang kita bangun:**

| Alasan | Berapa kali | Bisa dicegah sistem? |
|---|---|---|
| Lampiran wajib kontrak kurang | | ya — checklist gabungan |
| Foto evidence tidak lengkap/tidak jelas | | ya — ODK |
| Nomor dokumen salah/tidak sesuai format kontrak | | ya — numbering service |
| Nilai/volume tidak cocok dengan kontrak | | ya — consistency checker |
| Salah nama pihak / jabatan penanda tangan | | ya — ekstraktor kontrak |
| | | |

---

## 3. Pengetikan berulang (diagnosis hlm. 5 no. 1)

| Yang diukur | Angka |
|---|---|
| Satu data (mis. nama pekerjaan) diketik ulang di berapa dokumen | ___ |
| Menit untuk menyiapkan 1 SPPH ke vendor | ___ |
| Menit mengetik ulang 1 SPH vendor jadi baris tabel banding | ___ |
| Rata-rata berapa vendor per pengadaan | ___ |
| **Total menit menyusun 1 tabel banding harga** | **___** |

> Angka baris ketiga adalah **target utama parser SPH Anda**. Tanpa ini, tidak ada cara
> membuktikan parser itu menolong.

**Cara mengukurnya paling jujur:** kerjakan sendiri 3 SPPH manual di bulan 1–2 sambil
menyalakan stopwatch (briefing hlm. 18 memang meminta ini).

---

## 4. Balasan vendor tak terlacak (diagnosis hlm. 5 no. 2)

| Yang diukur | Angka |
|---|---|
| SPPH terkirim per bulan | ___ |
| Berapa % dibalas vendor | ___% |
| Rata-rata hari sampai vendor membalas | ___ |
| Berapa kali per bulan harus menyusul manual | ___ |
| Pernahkah pengadaan tertunda karena balasan terlewat? Berapa kali | ___ |

---

## 5. Lampiran BAST tak terlacak (diagnosis hlm. 5 no. 4)

| Yang diukur | Angka |
|---|---|
| Rata-rata jumlah lampiran wajib per BAST | ___ |
| Di mana foto evidence disimpan sekarang | |
| Berapa kali per bulan foto hilang / harus diminta ulang ke teknisi | ___ |
| Kapan biasanya ketahuan lampiran kurang | ☐ saat mau tanda tangan ☐ saat ditolak klien |

---

## 6. Angka milik AI Engineer

Yang ini dari kode dan dataset, diisi otomatis dan tervalidasi oleh `eval.run_eval`:

| Yang diukur | Angka | Sumber |
|---|---|---|
| Dataset historis proyek BUT | 20 proyek, 75 dokumen PDF (27 SPH, 22 Kontrak/PKS, 15 BAST/BAUT, 11 Dokumen Pengadaan) | `storage/data_project_but_inventory.json` |
| Waktu proses per dokumen (RapidOCR) | 163–313 detik (tergantung ketebalan 2–14 hlm) | `storage/outputs/bench/_hasil/_ringkasan.json` |
| Rata-rata akurasi lenient (4 golden terverifikasi) | **0,890** | `eval/reports/batas_panjang_2026-09-30.json`, dihitung ulang tanpa golden rangka |
| Rata-rata akurasi field kritis (4 golden terverifikasi) | **0,972** | idem |
| Nilai salah yang tetap berstatus bukti kuat/cukup | 4 field | idem (`false_acceptance`) |
| Akurasi SPH vendor (SPH vendor A) | lenient 0,946 · kritis 1,000 | **n = 1** SPH vendor |
| Akurasi kontrak (3 dokumen) | lenient 0,750–0,967 · kritis 0,889–1,000 | semua hasil pindai, satu perusahaan dominan |
| Jumlah golden terverifikasi | 4 dari target ≥15 (3 kontrak, 1 SPH vendor); 2 rangka belum dikoreksi | `eval/golden/` |

> Laporan eval sebelum 2 Okt 2026 mencatat 0,926 / 0,982 karena ikut menghitung 2 golden
> rangka otomatis (skornya pasti 1,0). `eval/run_eval.py` kini mengecualikannya.

---

## Cara mengumpulkannya

**Minggu ini, 4 percakapan pendek:**

| Siapa | Tanya apa | Untuk bagian |
|---|---|---|
| Admin / staf pengadaan | §3 dan §4 — mereka yang mengetik ulang | pengetikan, SPPH |
| PM | §1 dan §2 — mereka yang kena tolak | cashflow, penolakan |
| Teknisi lapangan | §5 — mereka yang memotret | evidence |
| Finance | §1 baris invoice → uang masuk | cashflow |

**Yang tidak perlu ditanya** — cari sendiri di arsip: tanggal-tanggal di §1 ada di dokumen
proyek lama; jumlah BAST ditolak ada di email/WhatsApp.

Kalau satu angka tidak bisa didapat, **tulis "tidak tersedia" beserta alasannya** — jangan
dikosongkan. Baseline yang jujur dengan lubang lebih berguna daripada baseline yang ditebak.

---

**Diisi oleh:** ___  **Tanggal:** ___  **Ditinjau mentor:** ___
