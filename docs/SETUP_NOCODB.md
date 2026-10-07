# Menyiapkan NocoDB & mengirim hasil parser

Satu pertanyaan: **apa yang harus disiapkan supaya hasil parser masuk NocoDB dan bisa
dikonfirmasi PM?** Skema berasal dari repo `dol-schema` (versi `companion-2026.10.3`);
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
| SPH Vendor | `sph` | sistem |
| Rincian SPH | `sph_item` | sistem |
| Hasil Ekstraksi | `extracted_field` | sistem |
| **Keputusan PM** | `field_review` | **PM saja** |

Tidak dibuat: tabel BAST dan Foto Evidence (masih **usulan**, menunggu workshop) dan
Riwayat Pemrosesan (hanya PostgreSQL). Kolom per tabel: `../dol-schema/generated/KAMUS_DATA.md`.

---

## Langkah 1 — Token

NocoDB → foto profil → **Account Settings → Tokens → Add New Token**. Isi di `.env`:

```
NOCODB_URL=http://localhost:8080
NOCODB_API_TOKEN=<token>
```

## Langkah 2 — Buat base dari dol-schema

```bash
make buat-base                     # atau: make buat-base JUDUL="DOL Schema uji"
# -> base "DOL Schema 2026.10.3" + 9 tabel + 8 relasi Link, lalu mencetak:
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
2026.10.3. Base lama biarkan
sebagai arsip; jangan dihapus sebelum datanya tidak diperlukan lagi.

## Langkah 3 — Periksa

```bash
make cek-nocodb        # URL, token, setiap tableId
make cek-skema         # setiap tabel NocoDB identik dengan dol-schema, kolom per kolom
```

## Langkah 4 — Kirim satu dokumen

```bash
.venv311/bin/python scripts/companion.py --map storage/outputs/extraction/<dok>.extract.json
.venv311/bin/python scripts/companion.py --push storage/outputs/companion/<dok>.companion.json --dry-run
.venv311/bin/python scripts/companion.py --push storage/outputs/companion/<dok>.companion.json
```

Yang dilakukan pusher (`app/companion/nocodb_push.py`):

1. Validasi payload terhadap dol-schema; gagal = tidak ada yang dikirim.
2. **Tolak bila dokumen sudah punya Keputusan PM** — proses ulang tidak menimpa dokumen
   yang sedang/sudah diperiksa. Bila PM memang minta diproses ulang: `--timpa-yang-direview`.
3. Upsert per kunci anti-dobel (dokumen = sidik isi berkas; anak = induk + nomor urut).
4. Hapus baris anak yang tidak ada lagi di hasil baru (mis. kontrak kini terbaca 3 item,
   bukan 5) — kecuali masih dirujuk tabel lain.

---

## Tampilan untuk PM

1. **Grid Hasil Ekstraksi**, dikelompokkan per **ID Dokumen**, diurutkan **Status Bukti**:
   `bertentangan`, `tidak_ada_di_dokumen`, `perlu_dicek` lebih dulu. `bukti_kuat` artinya
   nilai *ditemukan* di dokumen — bukan berarti benar; harga tetap wajib dicek.
2. **Form Keputusan PM** — satu-satunya tempat PM mengetik: Nama Field, Nilai Sistem Saat
   Diperiksa, Keputusan (`benar` / `dikoreksi` / `ditolak`), Nilai Final, Diperiksa Oleh,
   Waktu Diperiksa, Catatan PM.
3. **Hak akses:** PM peran Editor. Token pipeline idealnya tanpa akses tulis ke Keputusan PM
   (pusher juga menolaknya, tapi batas akses lebih kuat dari kedisiplinan kode).

---

## Keputusan yang masih terbuka: PostgreSQL di bawah NocoDB?

Tabel yang dibuat lewat API NocoDB (langkah 2) **hanya punya kolom**: tanpa UNIQUE, CHECK,
foreign key, maupun view. Integritasnya dijaga pengirim — validator dol-schema dan pusher.
Kolom "ID Kontrak" berisi angka, bukan tautan yang bisa diklik.

Alternatifnya: jalankan `../dol-schema/generated/schema.sql` di PostgreSQL dan sambungkan
NocoDB sebagai *external data source*. Constraint dan view (`document_review_status`,
`nilai_terverifikasi`) lalu benar-benar berlaku. Konsekuensinya: NocoDB membuat judul kolom
dari nama kolom DB, jadi judul Indonesia harus diatur ulang di UI, dan pipeline sebaiknya
menulis langsung ke PostgreSQL (belum ada penulisnya di repo ini).

Ini keputusan #4a di `../dol-schema/docs/WORKSHOP_SKEMA.md` — diputuskan bersama, bukan
sendirian.
