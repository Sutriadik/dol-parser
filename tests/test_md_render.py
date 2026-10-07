"""
Unit tests untuk module md_render (aturan tunggal Markdown + inferensi hierarki heading).
"""

from app.parsers.md_render import (
    PenataList,
    block_to_markdown,
    infer_heading_markdown,
    pasang_penanda_list,
)


class TestHeadingHierarchy:
    def test_level_1_title(self):
        # Judul dokumen utama harus #
        assert (
            infer_heading_markdown("SURAT PERJANJIAN KERJASAMA", "title")
            == "# SURAT PERJANJIAN KERJASAMA"
        )
        assert (
            infer_heading_markdown("BERITA ACARA SERAH TERIMA", "section_header")
            == "# BERITA ACARA SERAH TERIMA"
        )
        assert (
            infer_heading_markdown("BAB I KETENTUAN UMUM", "section_header")
            == "# BAB I KETENTUAN UMUM"
        )

    def test_level_2_pasal_and_sections(self):
        # Pasal atau nomor utama harus ##
        assert (
            infer_heading_markdown("PASAL 1 LINGKUP PEKERJAAN", "section_header")
            == "## PASAL 1 LINGKUP PEKERJAAN"
        )
        assert (
            infer_heading_markdown("1. WAKTU PELAKSANAAN", "section_header")
            == "## 1. WAKTU PELAKSANAAN"
        )
        assert (
            infer_heading_markdown("A. HAK DAN KEWAJIBAN", "section_header")
            == "## A. HAK DAN KEWAJIBAN"
        )

    def test_level_3_sub_clauses(self):
        # Sub-klausul 1.1, 2.1 harus ###
        assert (
            infer_heading_markdown("1.1 Pekerjaan Pemeliharaan", "section_header")
            == "### 1.1 Pekerjaan Pemeliharaan"
        )
        assert (
            infer_heading_markdown("2.3 Pembayaran Termin", "section_header")
            == "### 2.3 Pembayaran Termin"
        )

    def test_level_4_sub_sub_clauses(self):
        # Sub-sub butir 1.1.1 harus ####
        assert (
            infer_heading_markdown("1.1.1 Rincian Ruang Lingkup", "section_header")
            == "#### 1.1.1 Rincian Ruang Lingkup"
        )

    def test_existing_markdown_hash_preserved(self):
        # Jika teks sudah memiliki format hash dari parser, pertahankan
        assert infer_heading_markdown("### Judul Custom", "section_header") == "### Judul Custom"


class TestBlockToMarkdown:
    def test_key_value(self):
        assert block_to_markdown("key_value", "Nama : Sutriadi") == "- **Nama** : Sutriadi"
        assert block_to_markdown("key_value", "Alamat : Bandung") == "- **Alamat** : Bandung"

    def test_list_item(self):
        assert block_to_markdown("list_item", "Butir pertama") == "- Butir pertama"
        assert block_to_markdown("list_item", "- Butir sudah ber-dash") == "- Butir sudah ber-dash"

    def test_footer_and_caption(self):
        assert (
            block_to_markdown("footer", "Halaman 1 dari 10") == "<!-- FOOTER: Halaman 1 dari 10 -->"
        )
        assert block_to_markdown("caption", "Tabel 1.1") == "_Tabel 1.1_"

    def test_image(self):
        assert block_to_markdown("image", "") == "<!-- IMAGE -->"
        assert block_to_markdown("image", "Logo Telkom") == "<!-- IMAGE: Logo Telkom -->"


def _render(*baris: str) -> list[str]:
    """Render beberapa blok list berturut-turut dengan satu penata, seperti di parser."""
    penata = PenataList()
    return [block_to_markdown("list_item", b, penata) for b in baris]


class TestTingkatList:
    """Tingkat ditentukan dari urutan munculnya gaya penanda -- bukan urutan baku."""

    def test_ayat_lalu_huruf_kontrak_telkom(self):
        # Kasus nyata Pasal 13: ayat (1) + butir a-c digabung satu blok oleh block_grouper.
        # Dulu hanya baris pertama yang diberi "- " dan penandanya hilang.
        teks = "(1) Yang dimaksud:\na. Bencana alam;\nb. Kegoncangan sosial;\nc. Peraturan."
        assert block_to_markdown("list_item", teks) == (
            "- (1) Yang dimaksud:\n"
            "   - a. Bencana alam;\n"
            "   - b. Kegoncangan sosial;\n"
            "   - c. Peraturan."
        )

    def test_daftar_yang_dibuka_huruf_ada_di_tingkat_nol(self):
        assert _render("a. Satu", "b. Dua") == ["- a. Satu", "- b. Dua"]

    def test_urutan_lain_huruf_besar_angka_huruf_kecil(self):
        hasil = _render("A. Umum", "1) Lingkup", "a) Rinci", "b) Rinci", "2) Harga", "B. Khusus")
        assert hasil == [
            "- A. Umum",
            "   1) Lingkup",
            "      - a) Rinci",
            "      - b) Rinci",
            "   2) Harga",
            "- B. Khusus",
        ]

    def test_romawi_besar_di_atas_huruf_dan_angka(self):
        assert _render("I. Pendahuluan", "A. Latar", "1. Detail", "II. Isi") == [
            "- I. Pendahuluan",
            "   - A. Latar",
            "      1. Detail",
            "- II. Isi",
        ]

    def test_i_setelah_h_adalah_huruf_bukan_romawi(self):
        hasil = _render(*[f"{c}. butir" for c in "abcdefghij"])
        assert all(baris.startswith("- ") for baris in hasil)

    def test_i_pembuka_di_bawah_huruf_adalah_romawi(self):
        assert _render("a. Induk", "i. Anak", "ii. Anak", "iii. Anak", "b. Induk") == [
            "- a. Induk",
            "   - i. Anak",
            "   - ii. Anak",
            "   - iii. Anak",
            "- b. Induk",
        ]

    def test_huruf_kapital_salah_ocr_tetap_satu_tingkat(self):
        # Kasus nyata Pasal 7: OCR membaca "c." sebagai "C." di antara "b." dan "d.".
        assert _render("(2) Syarat:", "a. Surat", "b. Kuitansi", "C. Faktur", "d. Pajak") == [
            "- (2) Syarat:",
            "   - a. Surat",
            "   - b. Kuitansi",
            "   - C. Faktur",
            "   - d. Pajak",
        ]

    def test_nomor_melompat_karena_ocr_tetap_tingkat_sama(self):
        hasil = _render("(1) Satu", "a. Butir", "(3) Tiga")
        assert hasil == ["- (1) Satu", "   - a. Butir", "- (3) Tiga"]

    def test_tingkat_berlanjut_antar_blok_dan_reset_di_judul(self):
        penata = PenataList()
        assert block_to_markdown("list_item", "(1) Ayat", penata) == "- (1) Ayat"
        assert block_to_markdown("list_item", "a. Butir", penata) == "   - a. Butir"
        block_to_markdown("section_header", "Pasal 8 DENDA", penata)
        assert block_to_markdown("list_item", "a. Butir", penata) == "- a. Butir"

    def test_nomor_angka_jadi_list_bernomor_markdown(self):
        # "- 1. teks" di CommonMark menjadi list bernomor di DALAM bullet; tulis langsung.
        assert block_to_markdown("list_item", "1. Surat Permohonan") == "1. Surat Permohonan"
        assert block_to_markdown("list_item", "2) Kuitansi") == "2) Kuitansi"

    def test_alamat_bukan_penanda(self):
        assert _render("(1) Ayat", "Jl. Merdeka No. 1") == ["- (1) Ayat", "- Jl. Merdeka No. 1"]


class TestPenandaList:
    """Docling menyimpan "(1)"/"a." di `item.marker`, terpisah dari `item.text`."""

    def test_penanda_dari_docling_ditempel_di_depan_teks(self):
        assert pasang_penanda_list("Bencana alam;", "a.") == "a. Bencana alam;"
        assert pasang_penanda_list("Yang dimaksud", "(1)") == "(1) Yang dimaksud"
        assert pasang_penanda_list("Pendahuluan", "IV.") == "IV. Pendahuluan"

    def test_penanda_kosong_atau_sudah_ada_tidak_digandakan(self):
        assert pasang_penanda_list("Majeur wajib", "") == "Majeur wajib"
        assert pasang_penanda_list("Majeur wajib", None) == "Majeur wajib"
        assert pasang_penanda_list("a. Bencana", "a.") == "a. Bencana"

    def test_bullet_simbol_dan_singkatan_tidak_ditempel(self):
        # Bullet sudah diwakili "- " oleh perender; "- • teks" hanya mengotori.
        assert pasang_penanda_list("Kuitansi", "•") == "Kuitansi"
        assert pasang_penanda_list("Kuitansi", "-") == "Kuitansi"
        assert pasang_penanda_list("Merdeka", "Jl.") == "Merdeka"
