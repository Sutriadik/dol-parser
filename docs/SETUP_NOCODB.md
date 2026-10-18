# Menyiapkan NocoDB & mengirim hasil parser

Satu pertanyaan: **apa yang harus disiapkan supaya hasil parser masuk NocoDB dan bisa
dikonfirmasi PM?** Skema berasal dari repo `dol-schema` (versi `companion-2026.10.5`);
jangan membuat tabel dengan tangan.

Semua perintah dijalankan dari folder `dol-parser/`, setelah memuat `.env`:

```bash
set -a; . ./.env; set +a          # app/config.py tidak membaca .env sendiri
```

---

## Yang dibuat di NocoDB

Hanya tabel yang **berlaku**. Judul tabel & kolom berbahasa Indonesia:

| Judul di NocoDB | Nama teknis | Diisi oleh |
|---|---|---|
| Dokumen | `document` | sistem |
| Kontrak | `contract` | sistem |
| Pihak Kontrak | `contract_party` | sistem |
| Rincian Kontrak | `contract_item` | sistem |
| Syarat Kontrak | `contract_requirement` | sistem |
| Ketentuan Pembayaran | `contract_payment_term` | sistem |
| SPH Vendor | `sph` | sistem |
| Rincian SPH | `sph_item` | sistem |
| Hasil Ekstraksi | `extracted_field` | sistem |
| **Keputusan PM** | `field_review` | **PM saja** |

Tidak dibuat: tabel BAST dan Foto Evidence (masih **usulan**, menunggu workshop) dan
Riwayat Pemrosesan (hanya PostgreSQL). Kolom per tabel: `../dol-schema/generated/KAMUS_DATA.md`.

---

## Langkah 1: Token

NocoDB → foto profil → **Account Settings → Tokens → Add New Token**. Isi di `.env`:

```
NOCODB_URL=http://localhost:8080
NOCODB_API_TOKEN=<token>
```

## Langkah 2: Buat base dari dol-schema

```bash
make buat-base                     # atau: make buat-base JUDUL="DOL Schema uji"
# -> base "DOL Schema 2026.10.5" + 10 tabel + 9 relasi Link, lalu mencetak:
#    NOCODB_BASE_ID=...
#    NOCODB_TABLE_IDS=document:...,contract:...,...,field_review:...
```

Salin dua baris itu ke `.env`. Aman diulang: tabel dan relasi yang sudah ada dipakai ulang.

**Relasi.** Setiap kolom FK di dol-schema (mis. `sph_item.sph_id`) dibuat sebagai relasi
Link. PM melihat kolom "SPH Vendor" berisi nomor SPH yang bisa diklik di Rincian SPH, dan
kolom "Rincian SPH" di tabel SPH Vendor. Kolom FK-nya sendiri ("ID SPH") tetap ada dan tetap
diisi pusher dengan Id induk.

Batasnya: NocoDB di basis data bawaannya **tidak menegakkan** relasi ini. FK ke Id yang
tidak ada tetap diterima, dan menghapus induk hanya mengosongkan FK anaknya. Kebenaran
relasi dijaga pusher (validasi dol-schema). Constraint sungguhan (`REFERENCES`, `UNIQUE`,
`CHECK`) baru berlaku bila NocoDB memakai PostgreSQL dari `dol-schema/generated/schema.sql`.

Base yang dibuat sebelum 2026-10-06 punya FK sebagai kolom Number biasa; `--create-base`
tidak mengubahnya di tempat. Buat base baru.

**Base versi lama tidak bisa dipakai.** Judul kolom dan pilihan nilai berubah di 2026.10.2
(mis. `auto_verified` → `bukti_kuat`), kunci dokumen kini sha256, dan kolom NPWP dihapus di
2026.10.3, 2026.10.4 menambah kolom Jenis Biaya (OTC/MRC) serta tabel Ketentuan
Pembayaran, dan 2026.10.5 menambah kolom Skor Keyakinan dan Mutu Pembacaan. Base lama biarkan
sebagai arsip; jangan dihapus sebelum datanya tidak diperlukan lagi.

## Langkah 3: Periksa

```bash
make cek-nocodb        # URL, token, setiap tableId
make cek-skema         # setiap tabel NocoDB identik dengan dol-schema, kolom per kolom
```

## Langkah 3b: Tampilan untuk PM

```bash
make buat-tampilan                 # atau: make buat-tampilan JUDUL="DOL Schema uji"
```

Menyiapkan base itu untuk PM. Aman diulang: kolom dan tampilan yang judulnya sudah ada
dilewati, jadi penyesuaian yang dibuat PM di NocoDB tidak tertimpa. Rumus kolom hitungan yang
diperbaiki di skrip ikut diperbarui. Butuh base 2026.10.7 ke atas (relasi Keputusan PM).

Alur PM: buka **Beranda PM**, pilih dokumen, periksa **Antrean PM** dan grid verifikasi
per baris, lalu tekan **Putuskan** atau **Cek Baris**. Tombol itu membuka form
*Catat Keputusan* yang sudah terisi Nama Field, nilai sistem, dokumen, baris yang
diputuskan, dan waktu. PM tinggal memilih `benar` / `dikoreksi` / `ditolak` dan menulis
namanya. Field yang sudah diputuskan hilang dari Antrean PM.

| Tabel | Tampilan | Isinya |
|---|---|---|
| Dokumen | **Beranda PM** | satu baris per dokumen: Status Pemeriksaan, Progres (%), jumlah field Perlu Dicek dan Sudah Diputuskan |
| Hasil Ekstraksi | **Antrean PM** | status `bertentangan`, `tidak_ada_di_dokumen`, `perlu_dicek` yang belum diputuskan, dikelompokkan per Dokumen lalu Bagian, paling berisiko di atas. Rincian item tidak diulang di sini |
| Hasil Ekstraksi | **Harga Wajib Dicek** | subtotal, PPN, total, terbilang, apa pun statusnya (harga selalu diverifikasi manusia) |
| Hasil Ekstraksi | **Semua Field per Dokumen** | semua field, dikelompokkan per Dokumen dan Bagian |
| Dokumen | **Dokumen Masuk** | terbaru di atas, dengan Mutu Pembacaan dan Catatan Validasi; Isi Dokumen disembunyikan |
| Rincian SPH | **Verifikasi SPH per Baris** | grid verifikasi (briefing Bulan 3): satu baris penawaran per baris grid, Cek Hitung, Keputusan, tombol Cek Baris |
| Rincian Kontrak | **Verifikasi Kontrak per Baris** | sama, untuk BoQ kontrak |
| Keputusan PM | **Riwayat Keputusan** | keputusan terbaru di atas |
| Keputusan PM | **Catat Keputusan** (form) | dibagikan lewat tautan supaya bisa diisi dari URL |

Kolom hitungan yang ditambahkan (tidak menyimpan data, `--compare-schema` melewatinya):
Field (nama ramah, mis. "Baris 2 · Harga Satuan"), Bagian, Berisiko, Urutan Risiko,
Keputusan, Jumlah Keputusan, Putuskan di Hasil Ekstraksi; Jumlah Field, Sudah Diputuskan,
Perlu Dicek, Progres, Status Pemeriksaan di Dokumen; Dokumen Asal, Cek Hitung, Keputusan,
Jumlah Keputusan, Cek Baris di Rincian SPH dan Rincian Kontrak. Status Bukti dan Keputusan
diberi warna sesuai `docs/proyek/08_UI_UX.md` §2. Tampilan PM dikunci supaya susunannya tidak
berubah tanpa sengaja; datanya tetap bisa diisi.

Yang perlu diketahui:

- **Tautan form bersifat publik.** Siapa pun yang bisa membuka alamat NocoDB dan memegang
  tautannya bisa mengisi form. Di laptop (localhost) aman; sebelum NocoDB bisa dijangkau dari
  jaringan kantor, beri kata sandi pada tautan form (Share > Password).
- **Alamat di tombol** memakai `NOCODB_URL`. Bila NocoDB dibuka PM lewat alamat lain (mis.
  di server), isi `NOCODB_PUBLIC_URL` sebelum menjalankan perintah ini.
- **Batas rumus NocoDB di SQLite** (basis data bawaannya): tidak ada FIND, SUBSTITUTE,
  VALUE, INT, maupun regex; perbandingan memakai `==`; URLENCODE atas teks gabungan yang
  panjang membuat tabel tidak bisa dibaca ("parser stack overflow"). Tes di
  `tests/test_companion_push.py` menjaga ketiganya.

Daftarnya ada di `TAMPILAN_PM`, `KOLOM_TAMPILAN`, dan `FORM_KEPUTUSAN`
(`scripts/nocodb_setup.py`) dan memakai nama teknis dol-schema. Untuk menambah tampilan,
tambah satu entri di sana.

## Langkah 4: Kirim satu dokumen

```bash
.venv311/bin/python scripts/companion.py --map storage/outputs/extraction/<dok>.extract.json
.venv311/bin/python scripts/companion.py --push storage/outputs/companion/<dok>.companion.json --dry-run
.venv311/bin/python scripts/companion.py --push storage/outputs/companion/<dok>.companion.json
```

Yang dilakukan pusher (`app/companion/nocodb_push.py`):

1. Validasi payload terhadap dol-schema; gagal = tidak ada yang dikirim.
2. **Tolak bila dokumen sudah punya Keputusan PM**: proses ulang tidak menimpa dokumen
   yang sedang/sudah diperiksa. Bila PM memang minta diproses ulang: `--timpa-yang-direview`.
3. Upsert per kunci anti-dobel (dokumen = sidik isi berkas; anak = induk + nomor urut).
4. Hapus baris anak yang tidak ada lagi di hasil baru (mis. kontrak kini terbaca 3 item,
   bukan 5), kecuali masih dirujuk tabel lain.

---

## Tampilan untuk PM

1. **Grid Hasil Ekstraksi**, dikelompokkan per **ID Dokumen**, diurutkan **Status Bukti**:
   `bertentangan`, `tidak_ada_di_dokumen`, `perlu_dicek` lebih dulu. `bukti_kuat` artinya
   nilai *ditemukan* di dokumen, bukan berarti benar; harga tetap wajib dicek.
2. **Form Keputusan PM**: satu-satunya tempat PM mengetik: Nama Field, Nilai Sistem Saat
   Diperiksa, Keputusan (`benar` / `dikoreksi` / `ditolak`), Nilai Final, Diperiksa Oleh,
   Waktu Diperiksa, Catatan PM.
3. **Hak akses:** PM peran Editor. Token pipeline idealnya tanpa akses tulis ke Keputusan PM
   (pusher juga menolaknya, tapi batas akses lebih kuat dari kedisiplinan kode).

---

## Keputusan yang masih terbuka: PostgreSQL di bawah NocoDB?

Tabel yang dibuat lewat API NocoDB (langkah 2) **hanya punya kolom**: tanpa UNIQUE, CHECK,
foreign key, maupun view. Integritasnya dijaga pengirim: validator dol-schema dan pusher.
Kolom "ID Kontrak" berisi angka, bukan tautan yang bisa diklik.

Alternatifnya: jalankan `../dol-schema/generated/schema.sql` di PostgreSQL dan sambungkan
NocoDB sebagai *external data source*. Constraint dan view (`document_review_status`,
`nilai_terverifikasi`) lalu berlaku. Konsekuensinya: NocoDB membuat judul kolom
dari nama kolom DB, jadi judul Indonesia harus diatur ulang di UI, dan pipeline sebaiknya
menulis langsung ke PostgreSQL (belum ada penulisnya di repo ini).

Ini keputusan #4a di `../dol-schema/docs/WORKSHOP_SKEMA.md`, diputuskan bersama, bukan
sendirian.
