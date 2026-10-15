"""
Pemasangan penandatangan menurut kolom tanda tangan (bbox Docling).

Kasus nyata (Nota Pesanan dan kontrak layanan, hasil scan): tanda tangan dua kolom diratakan
OCR menjadi satu urutan teks, lalu LLM memasang direktur penyedia sebagai wakil pemberi kerja
dan sebaliknya, dengan status bukti_kuat. Bbox masih menyimpan kolomnya: nama perusahaan kedua
pihak sejajar, nama dan jabatan penandatangan di bawah masing-masing.

Semua nama dan perusahaan di sini rekaan.
"""

from app.document_ir.models import DocumentBlock, DocumentIR, DocumentPage
from app.extractors.reconcile import pasangkan_penandatangan
from app.schemas.evidence import ValidationReport
from app.validation.rules import note_penandatangan

PP, PK = "PT PEMBERI KERJA NUSANTARA TBK", "PT PENYEDIA JAYA ABADI"


def _blok(i, teks, x0, y0, x1, y1, page=2):
    return DocumentBlock(
        document_id="d",
        block_id=f"b{page}-{i}",
        page=page,
        text=teks,
        bbox=(x0, y0, x1, y1),
        reading_order=i,
        source_engine="uji",
    )


def _ir(*halaman):
    return DocumentIR(
        document_id="d",
        file_name="uji.pdf",
        source_engine="uji",
        pages=[DocumentPage(page_number=n, blocks=b) for n, b in halaman],
    )


def _ttd_dua_kolom(
    page=2, kiri="ANDI WIJAYA SENIOR MANAGER OPERASI", kanan="BUDI SANTOSO DIREKTUR"
):
    return (
        page,
        [
            _blok(0, PP, 0.14, 0.60, 0.46, 0.62, page),
            _blok(1, PK, 0.58, 0.60, 0.84, 0.62, page),
            _blok(2, "METERAI TEMPEL 5F2AOX0862", 0.20, 0.66, 0.36, 0.70, page),
            _blok(3, kiri, 0.15, 0.74, 0.45, 0.78, page),
            _blok(4, kanan, 0.60, 0.74, 0.80, 0.77, page),
        ],
    )


def _data(rep1=None, jab1=None, rep2=None, jab2=None):
    return {
        "Pihak Pertama": {"Nama Perusahaan": PP, "Nama Representative": rep1, "Jabatan": jab1},
        "Pihak Kedua": {"Nama Perusahaan": PK, "Nama Representative": rep2, "Jabatan": jab2},
    }


def test_wakil_yang_tertukar_dikembalikan_ke_kolomnya():
    data = _data("BUDI SANTOSO", "DIREKTUR", "ANDI WIJAYA", "SENIOR MANAGER OPERASI")

    perubahan = pasangkan_penandatangan(data, _ir(_ttd_dua_kolom()))

    assert data["Pihak Pertama"]["Nama Representative"] == "ANDI WIJAYA"
    assert data["Pihak Pertama"]["Jabatan"] == "SENIOR MANAGER OPERASI"
    assert data["Pihak Kedua"]["Nama Representative"] == "BUDI SANTOSO"
    assert data["Pihak Kedua"]["Jabatan"] == "DIREKTUR"
    assert {p["jenis"] for p in perubahan} == {"diganti"}


def test_wakil_yang_benar_tidak_diubah():
    data = _data("Andi Wijaya", "Senior Manager Operasi", "Budi Santoso", "Direktur")

    assert pasangkan_penandatangan(data, _ir(_ttd_dua_kolom())) == []
    assert data["Pihak Pertama"]["Nama Representative"] == "Andi Wijaya"


def test_wakil_kosong_diisi_dari_kolom_dan_teks_meterai_dibuang():
    data = _data()

    perubahan = pasangkan_penandatangan(data, _ir(_ttd_dua_kolom()))

    assert data["Pihak Pertama"]["Nama Representative"] == "ANDI WIJAYA"
    assert data["Pihak Pertama"]["Jabatan"] == "SENIOR MANAGER OPERASI"
    assert data["Pihak Kedua"]["Nama Representative"] == "BUDI SANTOSO"
    assert {p["jenis"] for p in perubahan} == {"diisi"}


def test_nama_rusak_di_satu_salinan_dikalahkan_salinan_yang_utuh():
    """Kasus nyata: satu salinan tanda tangan terbaca "BUDA", salinan lain "BUDI SANTOSO"."""
    data = _data()
    ir = _ir(
        _ttd_dua_kolom(page=2, kanan="BeuTeloMn BUDA DIREKTUR"),
        _ttd_dua_kolom(page=3, kanan="Be Tel - U BUDI SANTOSO DIREKTUR"),
    )

    pasangkan_penandatangan(data, ir)

    assert data["Pihak Kedua"]["Nama Representative"] == "BUDI SANTOSO"
    assert data["Pihak Kedua"]["Jabatan"] == "DIREKTUR"


def test_kepala_kolom_yang_tergabung_satu_blok_tidak_dipakai():
    """Kedua nama perusahaan dalam satu blok: kolom tidak bisa ditentukan, jadi tidak ada yang
    diubah walaupun LLM mengosongkan wakilnya."""
    data = _data()
    ir = _ir(
        (
            2,
            [
                _blok(0, f"{PP} {PK}", 0.14, 0.60, 0.84, 0.62),
                _blok(1, "ANDI WIJAYA SENIOR MANAGER OPERASI", 0.15, 0.74, 0.45, 0.78),
                _blok(2, "BUDI SANTOSO DIREKTUR", 0.60, 0.74, 0.80, 0.77),
            ],
        )
    )

    assert pasangkan_penandatangan(data, ir) == []
    assert data["Pihak Pertama"]["Nama Representative"] is None


def test_daftar_mewakili_tanpa_pasangan_sejajar_bukan_kolom_tanda_tangan():
    """Kasus nyata: 'Mewakili PT ...' di daftar penandatangan Berita Acara ada di kolom kiri,
    padahal tanda tangan pihak itu di kolom kanan."""
    data = _data()
    ir = _ir(
        (
            2,
            [
                _blok(0, f"Mewakili {PK}", 0.16, 0.48, 0.44, 0.49),
                _blok(1, "ANDI WIJAYA SENIOR MANAGER OPERASI", 0.15, 0.55, 0.45, 0.58),
            ],
        )
    )

    assert pasangkan_penandatangan(data, ir) == []


def test_blok_nama_selebar_dua_kolom_tidak_dipakai():
    """Kasus nyata (SPK): nama kedua penandatangan tergabung dalam satu blok selebar halaman."""
    data = _data()
    ir = _ir(
        (
            2,
            [
                _blok(0, "PIHAK PERTAMA", 0.14, 0.71, 0.27, 0.73),
                _blok(1, "PIHAK KEDUA", 0.65, 0.71, 0.75, 0.73),
                _blok(2, "ANDI WIJAYA BUDI SANTOSO DIREKTUR", 0.11, 0.77, 0.80, 0.83),
            ],
        )
    )

    assert pasangkan_penandatangan(data, ir) == []


def test_jabatan_dicari_dari_salinan_lain_bila_kolom_hanya_memuat_nama():
    data = _data()
    ir = _ir(
        _ttd_dua_kolom(page=2, kanan="BUDI SANTOSO"),
        (4, [_blok(0, "BUDI SANTOSO DIREKTUR", 0.60, 0.47, 0.79, 0.50, page=4)]),
    )

    pasangkan_penandatangan(data, ir)

    assert data["Pihak Kedua"]["Jabatan"] == "DIREKTUR"


def test_perubahan_masuk_validasi_sebagai_alasan_per_field():
    data = _data("BUDI SANTOSO", "DIREKTUR", None, None)
    perubahan = pasangkan_penandatangan(data, _ir(_ttd_dua_kolom()))

    laporan = note_penandatangan(ValidationReport(status="pass", rule_version="uji"), perubahan)

    isu = laporan.issues_for("Pihak Pertama.Nama Representative")
    assert isu and "kolom tanda tangan" in isu[0].message
    assert laporan.status == "warn"


def test_jabatan_dua_huruf_dan_meterai_varian_ocr():
    """Kasus nyata: "SM REGIONAL ..." dulu kehilangan "SM" sehingga seluruh baris dianggap nama,
    dan "MMTERAI" (OCR) terbaca sebagai nama."""
    data = _data()
    ir = _ir(_ttd_dua_kolom(kiri="MMTERAI TEMPEL ANDI WIJAYA SM OPERASI REGIONAL II"))

    pasangkan_penandatangan(data, ir)

    assert data["Pihak Pertama"]["Nama Representative"] == "ANDI WIJAYA"
    assert data["Pihak Pertama"]["Jabatan"] == "SM OPERASI REGIONAL II"


def test_daftar_tembusan_di_bawah_tanda_tangan_diabaikan():
    data = _data()
    halaman, blok = _ttd_dua_kolom()
    blok = [b for b in blok if b.block_id != f"b{halaman}-3"] + [
        _blok(5, "Tembusan: - GM Wilayah Timur - Manager Proyek II", 0.10, 0.80, 0.45, 0.88)
    ]

    pasangkan_penandatangan(data, _ir((halaman, blok)))

    assert data["Pihak Pertama"]["Nama Representative"] is None


def test_ejaan_nama_digabung_dari_beberapa_salinan():
    data = _data()
    ir = _ir(
        _ttd_dua_kolom(page=2, kiri="ANDI WIJ'AYA SM OPERASI"),
        _ttd_dua_kolom(page=3, kiri="ANDI WJAYA SM OPERASI"),
    )

    pasangkan_penandatangan(data, ir)

    assert data["Pihak Pertama"]["Nama Representative"] == "ANDI WIJAYA"


def test_penandatangan_tunggal_surat_hormat_kami_mengisi_wakil_kosong():
    """Kasus nyata (SPK dari BUT ke vendor): hanya penerbit yang menandatangani, dengan pola
    "Hormat Kami, a.n <perusahaan> / <jabatan>" lalu nama di bawahnya. Teks logo menempel pada
    nama; nama asli muncul lagi di salinan halaman lain."""
    data = _data()
    ir = _ir(
        (
            1,
            [
                _blok(0, f"Hormat Kami, a.n {PP}\nDirektur", 0.18, 0.69, 0.41, 0.72, page=1),
                _blok(1, "Be Tel\nAndi\nWijaya", 0.17, 0.81, 0.33, 0.86, page=1),
            ],
        ),
        (
            3,
            [
                _blok(0, f"Hormat Kami, a.n {PP}\nDirektur", 0.15, 0.34, 0.38, 0.37, page=3),
                _blok(1, "tel - U\nAndi Wijaya", 0.15, 0.45, 0.31, 0.48, page=3),
            ],
        ),
    )

    perubahan = pasangkan_penandatangan(data, ir)

    assert data["Pihak Pertama"]["Nama Representative"] == "Andi Wijaya"
    assert data["Pihak Pertama"]["Jabatan"] == "Direktur"
    assert data["Pihak Kedua"]["Nama Representative"] is None
    assert [p["jenis"] for p in perubahan] == ["diisi"]


def test_penandatangan_tunggal_tidak_mengganti_nilai_llm():
    data = _data(rep1="Siapa Saja", jab1="Direktur")
    ir = _ir(
        (
            1,
            [
                _blok(0, f"Hormat Kami, a.n {PP}\nDirektur", 0.18, 0.69, 0.41, 0.72, page=1),
                _blok(1, "Andi Wijaya", 0.17, 0.81, 0.33, 0.86, page=1),
                _blok(2, "Andi Wijaya", 0.17, 0.91, 0.33, 0.93, page=1),
            ],
        )
    )

    assert pasangkan_penandatangan(data, ir) == []
    assert data["Pihak Pertama"]["Nama Representative"] == "Siapa Saja"
