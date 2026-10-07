# Perbandingan Metode OCR — Ekstraksi Kontrak

Dokumen ini mengisi lembar Excel "Perbandingan Metode OCR - Ekstraksi Kontrak". Isinya
hasil pengukuran di mesin ini, bukan angka dari brosur atau benchmark orang lain.

## Cara mengukurnya

**Mesin yang diuji (5).** Semuanya berjalan lokal, tidak ada yang memanggil API berbayar.

| Baris di Excel | Yang sebenarnya dijalankan |
|---|---|
| Docling bawaan | Docling + **EasyOCR** — OCR default bawaan Docling |
| PaddleOCR | Jalur PP-Structure tersendiri (bukan lewat Docling) |
| Tesseract | Docling + Tesseract CLI 5.5.3 (`ind+eng`) |
| Apple Vision | Docling + `OcrMacOptions` (hanya macOS) |
| RapidOCR | Docling + RapidOCR ONNX, `lang=["latin"]` (PP-OCRv5) |

> **Pembaruan 2026-10-07:** EasyOCR dibuang dari proyek berdasarkan pengukuran ini
> (akurasi 47,3%, terendah kedua, dan runtuh ke 15,4% pada Nota Pesanan). Barisnya tetap
> di sini sebagai catatan. `bench_ocr.py --all` kini menjalankan 4 mesin.

Empat dari lima berjalan di atas Docling, jadi layout + TableFormer-nya sama; yang berbeda
benar-benar hanya mesin OCR-nya. PaddleOCR adalah pengecualian — ia memakai jalur parser
sendiri, jadi perbedaan hasilnya bukan murni perbedaan OCR. Ini disebut di sini supaya
tidak disalahartikan.

**Dokumen uji (3), set yang sama untuk semua mesin.** Ketiganya hasil scan bertanda tangan
— tidak ada yang punya lapisan teks, jadi semua mesin benar-benar harus meng-OCR.

| Dokumen | Halaman | Bentuk | Field diharapkan | Baris tabel |
|---|---|---|---|---|
| KL FULL SIGNED | 14 | Kontrak Layanan, tabel harga 5 baris + kolom OTC/MRC | 54 | 5 |
| SPK ATS Oracle | 3 | SPK, tabel 1 baris, ada PPN | 30 | 1 |
| NP Diskominfo kabupaten | 3 | Nota Pesanan, tabel 1 baris, tanpa PPN | 26 | 1 |
| **Total** | **20** | | **110** | **7** |

**Acuan kebenarannya dari mana.** Ketiga berkas golden di `eval/golden/` dibaca manual dari
PDF aslinya, bukan disalin dari keluaran model mana pun — kalau acuannya berasal dari model,
yang terukur cuma kemiripan antar-model. Tabel KL dicek silang secara aritmetika:
20.790.000 + 1.260.000 + 630.000 + 1.260.000 + 1.575.000 = 25.515.000 per bulan, dikali 12
bulan = 306.180.000, sama dengan Grandtotal tercetak. Jadi nilai SSL memang 7.560.000 dan
item keempat memang APJII.

**Arti tiap kolom.** Mengikuti panduan pengisian di lembar Excel:

- *Total Field Diharapkan* = field yang **memang ada isinya** di dokumen asli. Field yang di
  dokumennya memang kosong tidak ikut dihitung, supaya tidak ada nilai gratis.
- *Field Terisi* = dari field itu, berapa yang diisi pipeline (apa pun isinya).
- *Field Terisi Benar* = dari yang terisi, berapa yang isinya benar setelah dicocokkan ke
  dokumen asli.
- *Completeness Rate* = Field Terisi / Total Field Diharapkan.
- *Accuracy Rate* = Field Terisi Benar / Total Field Diharapkan.

**Dua hal yang diukur terpisah, dan ini penting.** Skor field di atas mengukur OCR **dan**
LLM sekaligus. Kalau sebuah angka salah, dari skor itu saja tidak ketahuan apakah OCR salah
baca atau LLM salah pungut. Karena itu ada pengukuran kedua (skrip lokal `probe_ocr.py`; tidak di repo karena memuat string dari dokumen asli): mencari string
sulit — nomor kontrak, angka rupiah berpemisah titik, singkatan — langsung di teks OCR
mentah, sebelum LLM menyentuhnya. Itu ukuran mutu OCR yang sesungguhnya.

**Konsistensi antar-run** diukur di tahap OCR: dokumen yang sama di-parse 2×, lalu teks
hasilnya dibandingkan lewat hash. LLM-nya sama untuk semua metode (`temperature=0.0`), jadi
mengukur ujung-ke-ujung justru mengaburkan perbedaan yang sedang dicari.

**Yang TIDAK diukur di sini**, supaya jelas batasnya: ketiga dokumen berasal dari dua
penerbit (Telkom dan Universitas Telkom) dan semuanya berbahasa Indonesia. Kolom "Tahan
Variasi Layout" dijawab dari 3 bentuk dokumen yang berbeda, bukan dari puluhan — jadi itu
indikasi, bukan jaminan.

## Cara mengulang pengukuran ini

```bash
.venv311/bin/python scripts/bench_ocr.py --all         # 4 mesin x 3 dokumen, bisa dilanjutkan
.venv311/bin/python scripts/skor_ocr.py                # skor field terhadap golden
.venv311/bin/python scripts/bench_ocr.py --consistency rapidocr --doc spk
```
