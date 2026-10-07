"""
Open ADE — Schema: Berita Acara Serah Terima (BAST)

BAST adalah dokumen serah terima yang memicu penagihan (lihat Delivery Ops Layer briefing:
"BAST Pelanggan yang menahan cashflow"). Berbeda dari Kontrak/SPH, tabel isinya biasanya
TANPA kolom harga (hanya daftar barang/pekerjaan yang diserahkan), dan seringkali dalam satu
file yang sama juga memuat "Berita Acara Uji Terima" (verifikasi teknis hasil pekerjaan) --
karena itu field hasil uji terima dibuat sebagai bagian opsional dari skema yang sama.

Field pihak memakai PihakDetail yang sama dengan skema kontrak (konsisten & bisa null
untuk representative/jabatan/alamat) karena BAST juga memuat blok "PIHAK PERTAMA/KEDUA"
atau "PIHAK KESATU/KEDUA" dengan struktur identik.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extractors.deterministic.identifiers import normalize_document_number
from app.schemas.contract import PihakDetail


class BASTItemDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor: str | None = Field(
        None, alias="No", description="Nomor urut baris barang/pekerjaan yang diserahkan"
    )
    deskripsi: str = Field(
        ...,
        alias="Deskripsi",
        description="Nama/uraian barang atau pekerjaan yang diserahterimakan",
    )
    volume: float | None = Field(None, alias="Volume", description="Jumlah/kuantitas barang")
    satuan: str | None = Field(
        None, alias="Satuan", description="Satuan unit (Unit, Paket, Lisensi, dsb)"
    )
    hasil_uji: str | None = Field(
        None,
        alias="Hasil Uji Terima",
        description="'Baik' atau 'Tidak' jika dokumen memiliki kolom uji terima per-item",
    )
    keterangan: str | None = Field(
        None, alias="Keterangan", description="Catatan tambahan pada baris"
    )
    extra_attributes: dict[str, Any] | None = Field(
        None, alias="Atribut Tambahan", description="Kolom tambahan lain dari tabel jika ada"
    )


class BASTExtractionSchema(BaseModel):
    """
    Sebagian besar field Optional secara sengaja: BAST bervariasi tergantung klien/proyek --
    beberapa mencantumkan nilai pengadaan, beberapa tidak; beberapa memuat bagian "Uji Terima",
    beberapa tidak. Nilai yang tidak ada di dokumen WAJIB null, bukan dikarang LLM.
    """

    model_config = ConfigDict(populate_by_name=True)

    nomor_bast: str | None = Field(
        None, alias="Nomor BAST", description="Nomor resmi dokumen BAST, jika dicantumkan"
    )
    nama_pekerjaan: str | None = Field(
        None, alias="Nama Pekerjaan", description="Judul pekerjaan/pengadaan yang diserahterimakan"
    )
    nomor_po_kontrak: str | None = Field(
        None,
        alias="Nomor PO / Kontrak",
        description="Nomor PO/SPK/Kontrak/Nota Pesanan rujukan yang mendasari BAST",
    )
    tanggal_po_kontrak: str | None = Field(
        None, alias="Tanggal PO / Kontrak", description="Tanggal PO/SPK/Kontrak rujukan"
    )
    tanggal_serah_terima: str | None = Field(
        None,
        alias="Tanggal Serah Terima",
        description="Tanggal pelaksanaan serah terima (format: YYYY-MM-DD atau teks asli)",
    )
    tanggal_aktivasi_layanan: str | None = Field(
        None,
        alias="Tanggal Aktivasi Layanan",
        description="Tanggal layanan mulai aktif/berjalan (misal kalimat 'Layanan telah aktif sejak ...'), "
        "relevan untuk layanan berlangganan/recurring. Null jika dokumen tidak menyebutkannya.",
    )

    pihak_pertama: PihakDetail = Field(
        ...,
        alias="Pihak Pertama",
        description="Pihak penerima barang/pekerjaan (Pemberi Kerja / Klien / Pemesan). Kadang disebut PIHAK KESATU",
    )
    pihak_kedua: PihakDetail = Field(
        ...,
        alias="Pihak Kedua",
        description="Pihak penyedia/pelaksana yang menyerahkan barang/pekerjaan",
    )

    # default_factory: baris tabel diisi deterministik dari parser (_reconcile_bast_items), jadi
    # pass-1 LLM tidak perlu mengetik ulang seluruh tabel serah terima.
    items: list[BASTItemDetail] = Field(
        default_factory=list,
        alias="Daftar Barang/Pekerjaan Diserahkan",
        description="Rincian seluruh baris barang/pekerjaan pada tabel serah terima",
    )

    nilai_pengadaan: float | None = Field(
        None,
        alias="Nilai Pengadaan",
        description="Nilai total pengadaan/pekerjaan dalam Rupiah, jika dicantumkan di BAST",
    )
    pernyataan_penerimaan: str | None = Field(
        None,
        alias="Pernyataan Penerimaan",
        description="Kalimat pernyataan kondisi barang/hasil pekerjaan diterima (misal: 'diterima dalam kondisi baik, lengkap, dan sesuai')",
    )

    tanggal_uji_terima: str | None = Field(
        None,
        alias="Tanggal Uji Terima",
        description="Tanggal pelaksanaan Berita Acara Uji Terima, jika dokumen memuat bagian ini",
    )
    hasil_uji_terima: str | None = Field(
        None,
        alias="Hasil Uji Terima Keseluruhan",
        description="Kesimpulan akhir uji terima jika ada, misal 'DITERIMA DENGAN HASIL BAIK DAN LENGKAP'",
    )

    dokumen_pendukung: list[str] | None = Field(
        None,
        alias="Dokumen Pendukung",
        description="Daftar lampiran wajib yang disebutkan, misal Purchase Order, Delivery Order/Surat Jalan",
    )
    daftar_tabel_terstruktur: list[dict[str, Any]] | None = Field(
        None,
        alias="Daftar Tabel Terstruktur",
        description="Representasi tabel utuh dari dokumen jika ada tabel tambahan",
    )

    @field_validator("nomor_bast", "nomor_po_kontrak", mode="after")
    @classmethod
    def _rapikan_nomor_dokumen(cls, v):
        """Buang spasi sisipan OCR di dalam nomor dokumen (lihat deterministic/identifiers.py)."""
        return normalize_document_number(v)
