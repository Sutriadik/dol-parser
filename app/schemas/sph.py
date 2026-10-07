"""
Open ADE — Schema: Surat Penawaran Harga (SPH) / Quotation

Schema khusus untuk dokumen penawaran harga vendor,
termasuk license, hardware, dan jasa IT.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.extractors.deterministic.identifiers import normalize_document_number


class SPHVendorInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nama_vendor: str = Field(
        ..., alias="Nama Vendor", description="Nama perusahaan yang mengajukan penawaran harga"
    )
    alamat_vendor: str | None = Field(
        None, alias="Alamat Vendor", description="Alamat kantor vendor"
    )
    kontak_vendor: str | None = Field(
        None, alias="Kontak / Email", description="No telepon atau email vendor"
    )
    npwp: str | None = Field(None, alias="NPWP", description="Nomor Pokok Wajib Pajak vendor")


class SPHItemDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    nomor: str | None = Field(
        None,
        alias="No",
        description="Nomor urut baris item penawaran dalam tabel secara berurutan (misal: 1, 2, 3, dst.)",
    )
    kategori: str | None = Field(
        None,
        alias="Kategori/Kelompok",
        description="Kategori kelompok pekerjaan atau sub-heading tabel (misal: A. Penyediaan Router Manage Service, B. Sharing Knowledge, Tenaga Ahli, Hardware, Lisensi)",
    )
    nama_item: str = Field(
        ...,
        alias="Nama Barang/Jasa",
        description="Uraian redaksi lengkap nama barang, pekerjaan, lisensi beserta seluruh rincian sub-item atau poin tugas secara utuh tanpa dipotong",
    )
    spesifikasi: str | None = Field(
        None,
        alias="Spesifikasi",
        description="Spesifikasi teknis, part number, tipe model, atau cakupan fitur barang/layanan",
    )
    brand_merek: str | None = Field(
        None,
        alias="Brand/Merek",
        description="Merek atau brand produk (misal: Fortinet, Cisco, Sophos)",
    )
    nomor_part: str | None = Field(
        None, alias="Part Number", description="Nomor part atau SKU produk"
    )
    volume: float = Field(..., alias="Volume / Qty", description="Jumlah kuantitas/volume barang")
    satuan: str = Field(
        ...,
        alias="Satuan",
        description="Satuan unit (Unit, Bulan, Lot, Pcs, License, Paket, Orang, Room, Unit/Bulan, dsb)",
    )
    periode: str | None = Field(
        None,
        alias="Periode/Durasi",
        description="Periode waktu atau durasi bulanan/harian jika ada (misal: 12 Bulan, 1 Year)",
    )
    harga_satuan: float = Field(
        ..., alias="Harga Satuan", description="Harga satuan per unit dalam Rupiah"
    )
    total_harga: float = Field(
        ..., alias="Total Harga", description="Total harga item dalam Rupiah"
    )
    # Diisi pembaca tabel dari judul kolom tempat angkanya berada, bukan oleh LLM: field item
    # dibuang dari skema pass-1 (DETERMINISTIC_FIELDS). Kosong bila judulnya tidak jelas.
    jenis_biaya: str | None = Field(
        None,
        alias="Jenis Biaya",
        description="OTC (sekali bayar), MRC (biaya bulanan), atau 'OTC dan MRC'",
    )
    keterangan: str | None = Field(
        None, alias="Keterangan", description="Catatan atau keterangan khusus pada baris tabel"
    )
    extra_attributes: dict[str, Any] | None = Field(
        None,
        alias="Atribut Tambahan",
        description="Kolom atau atribut fleksibel lainnya dari tabel (misal: Jumlah Titik/Group, OTC, MRC)",
    )


class SPHExtractionSchema(BaseModel):
    """
    Schema untuk Surat Penawaran Harga (SPH) Vendor — mendukung
    penawaran license, hardware, jasa IT, BoQ bertingkat, dan pengadaan umum.
    """

    model_config = ConfigDict(populate_by_name=True)
    vendor: SPHVendorInfo = Field(..., alias="Vendor")
    tujuan_surat: str | None = Field(
        None, alias="Tujuan Surat / Klien", description="Nama instansi/perusahaan yang dituju"
    )

    nomor_sph: str = Field(..., alias="Nomor SPH", description="Nomor surat penawaran harga")
    tanggal_sph: str = Field(..., alias="Tanggal SPH", description="Tanggal penawaran diterbitkan")
    perihal: str | None = Field(
        None, alias="Perihal / Nama Pekerjaan", description="Perihal surat penawaran harga"
    )

    masa_berlaku: str | None = Field(
        None,
        alias="Masa Berlaku Penawaran",
        description="Masa berlaku harga penawaran (misal: 30 hari)",
    )
    tanggal_berlaku_sampai: str | None = Field(
        None, alias="Berlaku Sampai Tanggal", description="Tanggal kadaluarsa penawaran"
    )
    jangka_waktu_pengiriman: str | None = Field(
        None,
        alias="Jangka Waktu Pengiriman",
        description="Waktu pelaksanaan atau pengiriman barang",
    )
    lokasi_pekerjaan: str | None = Field(
        None, alias="Lokasi Pekerjaan", description="Lokasi instalasi/pekerjaan"
    )

    # default_factory: baris tabel diisi deterministik dari parser (_reconcile_items), jadi
    # pass-1 LLM tidak perlu mengetik ulang seluruh BoQ.
    items: list[SPHItemDetail] = Field(
        default_factory=list,
        alias="Daftar Penawaran Harga",
        description="Rincian lengkap seluruh baris item penawaran (BoQ)",
    )

    subtotal: float = Field(..., alias="Subtotal", description="Total sebelum PPN")
    persentase_ppn: str | None = Field(
        None,
        alias="Persentase PPN",
        description="Persentase PPN yang tertulis di dokumen. Null jika tidak disebutkan.",
    )
    ppn_nominal: float | None = Field(
        None,
        alias="Nilai PPN",
        description="Nominal PPN yang tertulis di dokumen. Null jika tidak dicantumkan.",
    )
    grand_total: float = Field(
        ..., alias="Grand Total", description="Total akhir penawaran termasuk pajak"
    )
    jumlah_terbilang: str | None = Field(
        None,
        alias="Jumlah Terbilang",
        description="Nominal grand total penawaran yang ditulis dalam kata-kata, jika dicantumkan di dokumen",
    )

    mekanisme_pembayaran: str | None = Field(
        None, alias="Mekanisme Skema Pembayaran", description="Termin pembayaran yang ditawarkan"
    )
    garansi_layanan: str | None = Field(
        None, alias="Garansi / SLA", description="Garansi produk atau SLA layanan yang ditawarkan"
    )
    catatan_khusus: str | None = Field(
        None, alias="Catatan Khusus", description="Syarat dan ketentuan tambahan dari vendor"
    )
    syarat_ketentuan: list[str] | None = Field(
        None, alias="Syarat dan Ketentuan", description="Daftar syarat dan ketentuan penawaran"
    )
    daftar_tabel_terstruktur: list[dict[str, Any]] | None = Field(
        None,
        alias="Daftar Tabel Terstruktur",
        description="Representasi tabel utuh dari dokumen jika ada tabel tambahan",
    )

    @field_validator("nomor_sph", mode="after")
    @classmethod
    def _rapikan_nomor_dokumen(cls, v):
        """Buang spasi sisipan OCR di dalam nomor dokumen (lihat deterministic/identifiers.py)."""
        return normalize_document_number(v)
