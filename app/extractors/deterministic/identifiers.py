"""
Open ADE — Normalisasi identifier (nomor dokumen, rekening, NPWP).

Masalah yang ditangani. Nomor kontrak adalah field paling kritis di dokumen pengadaan:
salah satu karakter saja membuat BAST ditolak dan tagihan tertahan. Tetapi justru nomor
inilah yang paling sering dirusak OCR, karena bentuknya rapat dan berpindah-pindah antara
huruf besar dan angka:

    dokumen : K.TEL.054321/HK.810/T1R-0D000000/2026
    OCR     : K. TEL.054321/HK.810/T1R-0D000000/2026     <- spasi disisipkan setelah titik

Modul ini hanya membuang spasi yang MENEMPEL pada pemisah (titik, garis miring, strip).
Sengaja sesempit itu: spasi yang berdiri sendiri di antara dua kata bisa saja memang
bagian dari nomornya ("SPK 45/2026"), jadi tidak disentuh. Aturan yang lebih longgar
akan "memperbaiki" nomor yang sebenarnya sudah benar — dan kesalahan seperti itu tidak
ketahuan sampai BAST ditolak.

Yang TIDAK dilakukan modul ini: menukar karakter yang mirip (O/0, I/1). Menyamakan
keduanya saat MEMBANDINGKAN adalah toleransi yang wajar dan dilakukan di
`app/evidence/matcher.py`; menukarnya di dalam nilai yang disimpan berarti mengarang isi
dokumen.
"""

from __future__ import annotations

import re

# Bentuk identifier: ada digit, panjang wajar, hanya huruf/angka/pemisah.
_BENTUK_IDENTIFIER = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9\s./\-]{4,80}$")

# Spasi yang menempel pada pemisah. Contoh yang kena:
#   "K. TEL"   -> "K.TEL"      (spasi sesudah titik)
#   "HK .810"  -> "HK.810"     (spasi sebelum titik)
#   "T1R- 0D"  -> "T1R-0D"
_SPASI_SETELAH_PEMISAH = re.compile(r"(?<=[./\-])[ \t]+")
_SPASI_SEBELUM_PEMISAH = re.compile(r"[ \t]+(?=[./\-])")


def looks_like_document_number(value: str | None) -> bool:
    """
    Apakah nilai ini berbentuk nomor dokumen, bukan kalimat?

    Batas 6 kata ke bawah: nomor dokumen Indonesia terpanjang yang ditemui di 20 proyek
    nyata tetap di bawah itu, sementara deskripsi pekerjaan selalu jauh di atasnya.
    """
    if not isinstance(value, str):
        return False
    teks = value.strip()
    if not teks or len(teks.split()) > 6:
        return False
    return bool(_BENTUK_IDENTIFIER.match(teks))


def normalize_document_number(value: str | None) -> str | None:
    """
    Rapikan spasi sisipan OCR di dalam nomor dokumen. Nilai yang bukan nomor dokumen
    dikembalikan apa adanya.

        "K. TEL.054321/HK.810/T1R-0D000000/2026" -> "K.TEL.054321/HK.810/T1R-0D000000/2026"
        "77/00/HK-08/BUT/2025"                   -> tetap
        "Nomor 45 tahun 2026"                    -> tetap (spasi tidak menempel pemisah)
    """
    if not looks_like_document_number(value):
        return value
    teks = value.strip()
    dirapikan = _SPASI_SETELAH_PEMISAH.sub("", teks)
    dirapikan = _SPASI_SEBELUM_PEMISAH.sub("", dirapikan)
    return re.sub(r"\s{2,}", " ", dirapikan).strip()


# dua nomor kontrak
# Kata yang tidak membedakan satu perusahaan dari perusahaan lain.
_KATA_UMUM = {
    "pt",
    "cv",
    "ud",
    "pd",
    "persero",
    "tbk",
    "indonesia",
    "nusantara",
    "sejahtera",
    "mandiri",
    "utama",
    "jaya",
    "abadi",
    "group",
    "holding",
}


def _penanda_perusahaan(nama: str | None) -> set:
    """
    Penanda yang lazim dipakai di dalam nomor dokumen untuk menyebut satu perusahaan:
    akronim dari huruf awal tiap kata penting, plus potongan 3-4 huruf kata pertama.

        "PT BHAKTI UNGGUL TEKNOVASI"      -> {"BUT", "BHA", "BHAK"}
        "PT TELEKOMUNIKASI INDONESIA Tbk" -> {"T", "TEL", "TELE"}
    """
    if not isinstance(nama, str) or not nama.strip():
        return set()
    kata = [w for w in re.split(r"[^A-Za-z]+", nama.upper()) if w and w.lower() not in _KATA_UMUM]
    if not kata:
        return set()
    penanda = {"".join(w[0] for w in kata)}
    penanda |= {kata[0][:3], kata[0][:4]}
    return {p for p in penanda if len(p) >= 3}


def _segmen(nomor: str | None) -> set:
    """Potongan nomor dokumen yang dipisah / . - spasi, hanya yang berupa huruf."""
    if not isinstance(nomor, str):
        return set()
    return {s.upper() for s in re.split(r"[^A-Za-z0-9]+", nomor) if s.isalpha() and len(s) >= 2}


def _milik_siapa(nomor: str | None, penanda_p1: set, penanda_p2: set) -> str | None:
    """'pertama' | 'kedua' | None — hanya menjawab bila sinyalnya tidak ambigu."""
    seg = _segmen(nomor)
    if not seg:
        return None
    cocok_p1 = bool(seg & penanda_p1)
    cocok_p2 = bool(seg & penanda_p2)
    if cocok_p1 == cocok_p2:  # dua-duanya cocok atau dua-duanya tidak -> tidak menyimpulkan
        return None
    return "pertama" if cocok_p1 else "kedua"


def fix_contract_number_roles(
    nomor_kontrak: str | None,
    nomor_internal: str | None,
    nama_pihak_pertama: str | None,
    nama_pihak_kedua: str | None,
) -> tuple:
    """
    Tukar dua nomor kontrak bila keduanya jelas tertukar peran.

    Kontrak pengadaan Indonesia sering memuat DUA nomor untuk perjanjian yang sama: nomor
    versi pelanggan dan nomor versi penyedia. Keduanya berdampingan di halaman muka
    ("Nomor A dan Nomor B"), dan model rutin memasangkannya terbalik -- padahal
    `Nomor Kontrak Internal` menurut definisinya adalah "nomor versi pihak kedua".

    Penentunya ada di nomor itu sendiri: penyedia menyelipkan penanda perusahaannya di
    dalam nomornya.

        Pihak Pertama : PT TELEKOMUNIKASI INDONESIA Tbk
        Pihak Kedua   : PT BHAKTI UNGGUL TEKNOVASI
        K.TEL.012345/HK.810/T1R-0D000000/2025   -> segmen "TEL" -> pihak pertama
        77/00/HK-08/BUT/2025                    -> segmen "BUT" -> pihak kedua

    Ditukar HANYA bila kedua nomor sama-sama bisa disimpulkan dan keduanya menunjuk peran
    yang terbalik. Kalau salah satu ambigu, nilainya dibiarkan apa adanya: menukar
    berdasarkan tebakan pada field sepenting nomor kontrak lebih berbahaya daripada
    membiarkannya salah -- yang salah masih tertangkap konfirmasi per-field PM, yang
    tertukar diam-diam karena tebakan kita tidak.

    Mengembalikan (nomor_kontrak, nomor_internal) — sudah dalam urutan yang benar.
    """
    if not nomor_kontrak or not nomor_internal or nomor_kontrak == nomor_internal:
        return nomor_kontrak, nomor_internal

    p1 = _penanda_perusahaan(nama_pihak_pertama)
    p2 = _penanda_perusahaan(nama_pihak_kedua)
    if not p1 or not p2 or p1 & p2:  # penanda bertabrakan -> tidak bisa membedakan
        return nomor_kontrak, nomor_internal

    peran_utama = _milik_siapa(nomor_kontrak, p1, p2)
    peran_internal = _milik_siapa(nomor_internal, p1, p2)
    if peran_utama == "kedua" and peran_internal == "pertama":
        return nomor_internal, nomor_kontrak
    return nomor_kontrak, nomor_internal
