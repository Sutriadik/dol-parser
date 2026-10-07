# Integrasi n8n → FastAPI → NocoDB

Panduan merangkai alur yang diminta briefing hlm. 8: **n8n mengorkestrasi, FastAPI hanya
memparsing, NocoDB menyimpan.** FastAPI tidak menulis ke NocoDB dalam alur normal.

```
   PDF          n8n                 FastAPI (repo ini)              NocoDB
    │            │                         │                          │
    ├───────────>│  POST /api/v1/jobs ────>│  202 {job_id,            │
    │            │<────────────────────────┤      document_id}        │
    │            │                         │                          │
    │            │        (160-310 detik)  │ parse → OCR → LLM →      │
    │            │                         │ validate → evidence      │
    │            │<── POST callback_url ───┤  {companion_payload, ...} │
    │            │                                                    │
    │            ├── POST /api/v2/tables/{tableId}/records ──────────>│
```

---

## Kenapa asinkron, bukan sekali panggil

Benchmark nyata di `storage/outputs/bench/_hasil/_ringkasan.json`:

| Dokumen | Mesin OCR | Waktu total |
|---|---|---|
| SPK (6 hlm) | rapidocr | **163 detik** |
| NP (4 hlm) | rapidocr | **190 detik** |
| KL (10 hlm) | rapidocr | **313 detik** |

Node **HTTP Request** n8n default timeout 300 detik, dan proxy di depannya (nginx 60s,
Cloudflare 100s) memutus jauh lebih awal. Endpoint sinkron `/api/v1/process-all` karena itu
**timeout pada dokumen normal, bukan kasus ekstrem** — dan n8n akan mengirim ulang,
menumpuk antrian di mesin yang sama sampai makin lambat.

`/api/v1/process-all` tetap ada untuk uji manual dan `curl`. **Jangan dipakai dari n8n.**

---

## Endpoint

Semua endpoint di bawah butuh header `X-API-Key` bila `OPENADE_API_KEY` diisi di `.env`.

### `POST /api/v1/jobs` — kirim dokumen

`multipart/form-data`:

| Field | Wajib | Keterangan |
|---|---|---|
| `file` | ya | PDF/DOCX/PNG/JPG/TIFF, maks. `MAX_UPLOAD_MB` (default 50 MB) |
| `doc_type` | tidak | `auto` (default), `contract`, `sph`, `bast` |
| `ocr` | tidak | `rapidocr` (default), `mac`, `tesseract`, `paddle` |
| `max_pages` | tidak | batas halaman |
| `callback_url` | tidak | URL webhook n8n; hasil lengkap di-POST ke sini saat selesai |
| `push_to_nocodb` | tidak | `true` = worker menulis hasilnya ke NocoDB setelah ekstraksi. Butuh `NOCODB_PUSH_ENABLED=1`; kalau mati, ditolak **409** seketika, sebelum masuk antrian |

Balasan **202, seketika**:

```json
{
  "job_id": "job-d3eb741cabbf",
  "document_id": "doc-b9a77fc4c3265ed5",
  "status": "queued",
  "queue_position": 0,
  "poll_url": "/api/v1/jobs/job-d3eb741cabbf"
}
```

> **`document_id` adalah kunci idempotensi Anda.** Ia adalah sha256 isi berkas, dihitung
> sebelum dokumen diproses. PDF yang sama menghasilkan `document_id` yang sama — nama
> berkasnya boleh berbeda, mesin OCR-nya boleh berbeda. Itulah yang membuat push berulang
> ke NocoDB menjadi **UPDATE**, bukan baris kembar.

### `GET /api/v1/jobs/{job_id}` — polling

Selalu **200**, status ada di body (`queued` / `running` / `done` / `failed`). Kode HTTP
sengaja tidak dipakai untuk membedakan "belum selesai": node n8n menganggap kode non-2xx
sebagai error dan menghentikan alur.

Tambahkan `?include_result=true` untuk ikut menarik hasilnya. **Jangan pakai pada tiap
polling** — hasil memuat markdown penuh dan seluruh evidence, bisa puluhan MB.

### `GET /api/v1/jobs?document_id=doc-xxx` — cek pemrosesan ulang

Untuk tahu berkas ini sudah pernah atau sedang diproses, sebelum mengirim ulang.

---

## Rangkaian node di n8n

### Pilihan A — callback (disarankan)

1. **Webhook / Google Drive Trigger / IMAP** — sumber PDF.
2. **HTTP Request**
   - Method `POST`, URL `{{$env.OPENADE_URL}}/api/v1/jobs`
   - Header `X-API-Key: {{$env.OPENADE_API_KEY}}`
   - Body Content Type: **Form-Data Multipart**
   - Parameter `file` → tipe **n8n Binary File**, field `data`
   - Parameter `callback_url` → URL node **Wait** di langkah 3
3. **Wait**, Resume: **On Webhook Call**. FastAPI mem-POST hasil ke sini.
4. **HTTP Request** ke NocoDB, satu node per tabel (lihat bagian berikutnya).

### Pilihan B — polling (bila n8n di belakang NAT dan tidak bisa menerima callback)

Ganti langkah 3 dengan: **Wait 60 detik** → **HTTP Request** `GET /api/v1/jobs/{{job_id}}`
→ **IF** `status == "done"` → kalau belum, kembali ke Wait. Ambil `?include_result=true`
hanya pada iterasi terakhir.

---

## Memasukkan hasilnya ke NocoDB

Hasil job memuat `companion_payload`: baris per tabel dol-schema, dengan **nama teknis**
kolom. Untuk dokumen BAST nilainya `null` dan alasannya ada di `companion_catatan` (tabel
BAST masih usulan).

```
{ "document":             [ {...} ],
  "contract":             [ {"_document_ref": "doc-…", ...} ],
  "contract_item":        [ {"_contract_ref": "doc-…", "line_no": 1, ...}, ... ],
  "extracted_field":      [ ... ],
  "extraction_run":       [ ... ] }          <- jangan dikirim ke NocoDB
```

### Pilihan 1 — biarkan parser yang menulis (paling sederhana)

Set `NOCODB_PUSH_ENABLED=1` di `.env` service ini, lalu kirim `push_to_nocodb=true`
bersama berkasnya di `POST /api/v1/jobs`. Langkah 4 di Pilihan A tidak diperlukan lagi.
Semua aturan di bawah sudah ditangani `app/companion/nocodb_push.py`.

Cara tahu barisnya sudah masuk:

- Ringkasan job (`GET /api/v1/jobs/{job_id}`, juga di body callback) punya `nocodb_push`:
  `"ok"`, `"gagal: <sebab>"`, atau `null` bila push tidak diminta.
- Rincian per tabel ada di `result.nocodb_push`.

Push yang gagal (NocoDB mati, payload tidak lolos validasi dol-schema) **tidak** membuat
job `failed`: status tetap `done`, hasil ekstraksinya tetap bisa diambil. Jadi di n8n,
periksa `nocodb_push`, bukan hanya `status`.

`push_to_nocodb=true` juga ada di `/api/v1/process-all` (sinkron, untuk uji manual), dan
`scripts/companion.py --push` untuk berkas `.companion.json` yang sudah ada.

### Pilihan 2 — n8n menulis sendiri

Ikuti `../dol-schema/generated/schema.json`. Aturan yang wajib:

1. **Urutan:** `insert_order` (induk dulu). Tabel di luar daftar itu (usulan, audit)
   tidak dikirim.
2. **Judul, bukan nama teknis.** API rekaman NocoDB memakai **judul kolom** sebagai kunci
   JSON dan di `where`. Terjemahkan setiap kunci lewat `columns[].label` — mis.
   `contract_number` → `Nomor Kontrak`. Kolom `Id` tetap `Id`.
3. **Tukar placeholder induk.** `_contract_ref` berisi kunci dokumen induk; ganti dengan
   `contract_id` = Id baris Kontrak yang baru dibuat (judulnya `ID Kontrak`).
4. **Upsert:** `GET ...?where=(<judul kunci>,eq,<nilai>)` → ada: `PATCH` dengan `Id`;
   tidak ada: `POST`. Kunci per tabel ada di `upsert_key`.
5. **Hapus baris basi:** baris anak milik induk yang sama yang tidak ada di kiriman baru.
6. **Jangan timpa dokumen yang sudah diperiksa PM:** bila tabel Keputusan PM sudah punya
   baris untuk dokumen ini, berhenti dan beri tahu PM.
7. **Jangan kirim** kolom `human_columns`, tabel `field_review`, dan kolom
   `mybhakti_*_ref` dari payload parser (kolom itu diisi n8n sendiri dari MyBhakti;
   parser sengaja tidak mengirimnya).

`document_id` di balasan `/api/v1/jobs` **sama dengan** kunci baris Dokumen
(`content_hash`, judul `Sidik Berkas`), jadi n8n bisa mencari baris dokumennya langsung.

---

## Menjalankan servicenya

```bash
cp .env.example .env          # lalu isi OPENADE_API_KEY dan kredensial NocoDB
make serve                    # membaca .env; uvicorn langsung TIDAK membacanya
```

### Urutan uji SPH sampai masuk NocoDB

1. Nyalakan NocoDB (port 8080) dan Ollama (`open -a Ollama`).
2. `make buat-base` sekali per versi dol-schema. Salin `NOCODB_BASE_ID` dan
   `NOCODB_TABLE_IDS` yang dicetak ke `.env`, lalu set `NOCODB_PUSH_ENABLED=1`.
3. `make cek-nocodb` dan `make cek-skema` harus lolos semua.
4. `make serve`, lalu di terminal lain:
   `make uji-jobs FILE="sample_pdfs/....pdf" DOC_TYPE=sph`.
   Ini jalur yang sama dengan n8n (`/api/v1/jobs` + `push_to_nocodb=true`). Hasilnya
   lolos kalau `status` = `done` dan `nocodb_push` = `ok`.
5. Jalankan langkah 4 sekali lagi dengan berkas yang sama. Jumlah baris di NocoDB harus
   tetap, karena push ulang menjadi UPDATE.

### Alamat antar-layanan

Dari dalam container, `localhost` adalah container itu sendiri, bukan Mac Anda.

| Siapa memanggil siapa | Alamat |
|---|---|
| n8n (Docker) → API (di Mac, `make serve`) | `http://host.docker.internal:8000` |
| API (di Mac) → callback n8n | URL dari node Wait. Kalau n8n membuatnya dengan `localhost:5678`, itu sudah benar untuk API di Mac |
| API (Docker, `docker compose up api`) → NocoDB | `host.docker.internal:8080`, sudah diatur di `docker-compose.yml` (ganti lewat `NOCODB_URL_DARI_DOCKER`) |
| API (Docker) → callback n8n | n8n harus membuat resume URL yang bisa dijangkau container, mis. env n8n `WEBHOOK_URL=http://host.docker.internal:5678/` |

Cek kesiapan (endpoint ini sengaja tidak butuh API key, supaya bisa dipakai health check
Docker/uptime monitor):

```bash
curl -s localhost:8000/health | python -m json.tool
```

`status: "degraded"` berarti storage siap tapi Ollama mati — job akan masuk antrian lalu
gagal di tahap ekstraksi. Nyalakan `ollama serve` lebih dulu.

Uji satu dokumen tanpa n8n dan tanpa push (`make uji-jobs` di atas sudah melakukan ini
plus push):

```bash
curl -X POST localhost:8000/api/v1/jobs \
  -H "X-API-Key: $OPENADE_API_KEY" \
  -F "file=@dokumen.pdf" -F "doc_type=auto"
# lalu, dengan job_id dari balasan di atas:
curl "localhost:8000/api/v1/jobs/<job_id>?include_result=true" -H "X-API-Key: $OPENADE_API_KEY"
```

---

## Batas yang perlu diketahui

- **Satu dokumen pada satu waktu, sengaja.** Docling/PaddleOCR/Ollama lokal berebut CPU
  yang sama; memanggilnya paralel membuat keduanya lebih lambat, bukan lebih cepat.
  Antrian membuat perilaku itu terlihat (`queue_position`) alih-alih menyembunyikannya di
  balik lock. Perkiraan tunggu = `queue_position × ~250 detik`.
- **Status job disimpan di memori proses.** Restart uvicorn menghilangkan job yang sedang
  berjalan. Untuk satu instance ini memadai; penggantinya nanti Redis + RQ/Celery, bukan
  menambal `JobQueue`.
- **Callback dikirim sekali, tanpa retry.** Kalau n8n sedang restart, callback gagal tetapi
  job tetap `done` dan hasilnya masih bisa diambil lewat `GET /api/v1/jobs/{id}` —
  kerja 3-5 menit tidak terbuang. Alur n8n sebaiknya punya jadwal yang memeriksa
  `GET /api/v1/jobs` untuk job `done` yang `callback_status`-nya bukan `200`.
