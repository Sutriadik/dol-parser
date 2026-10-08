"""
Tests untuk app.parsers.block_grouper — murni fungsi, tanpa Docling/OCR, supaya cepat
dan tidak bergantung pada model. Skenario diambil dari pola nyata dokumen SPK/PKS
Indonesia (blok "Nama/Jabatan/Perusahaan" di bawah label PIHAK PERTAMA/KEDUA).
"""

from app.parsers.block_grouper import (
    DraftBlock,
    annotate_attestation,
    merge_adjacent_blocks,
    pair_labels_with_values,
    process_page_blocks,
    reading_order_sort,
)


def _block(type_, text, y0, y1, x0=0.1, x1=0.9, start=None, end=None):
    return DraftBlock(
        type=type_, text=text, bbox=(x0, y0, x1, y1), confidence=0.95, start=start, end=end
    )


def test_adjacent_same_family_blocks_merge():
    blocks = [
        _block("paragraph", "Nama : Rina Kartika", 0.10, 0.115, start=0, end=19),
        _block("paragraph", "Jabatan : Kepala Bidang", 0.117, 0.132, start=20, end=43),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].text == "Nama : Rina Kartika\nJabatan : Kepala Bidang"
    assert merged[0].start == 0 and merged[0].end == 43
    assert len(merged[0].atomic) == 2


def test_butir_list_digabung_diurutkan_dari_atas_ke_bawah():
    # Kasus nyata (kontrak, Pasal 13): Docling mengeluarkan "b." sebelum "a." walau bbox
    # "a." lebih tinggi; tanpa diurutkan, markdown menulis b-a-c.
    # Koordinat dari bbox asli halaman itu. "c." berjarak 0,029 dari "a." (di atas ambang)
    # tapi menempel ke "b." -- jarak harus diukur dari dasar grup, bukan dari anggota terakhir.
    a = "a. Bencana alam, diantaranya yaitu gempa bumi besar, angin topan, banjir besar;"
    b = "b. Kegoncangan sosial dalam masyarakat, diantaranya yaitu kerusuhan, pemogokan;"
    c = "c. Peraturan resmi Pemerintah dalam bidang moneter/ekonomi dan perdagangan."
    blocks = [
        _block("list_item", b, 0.307, 0.340),
        _block("list_item", a, 0.260, 0.308),
        _block("list_item", c, 0.337, 0.355),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].text == f"{a}\n{b}\n{c}"
    assert [m.text[:2] for m in merged[0].atomic] == ["a.", "b.", "c."]


def test_list_dua_kolom_tidak_diurutkan_ulang():
    # Kolom kiri & kanan tidak tumpang-tindih secara horizontal: urutan Docling dipertahankan.
    kiri = "a. Butir di kolom kiri yang cukup panjang supaya dianggap prosa biasa."
    kanan = "b. Butir di kolom kanan, posisinya sedikit lebih tinggi dari kolom kiri."
    blocks = [
        _block("list_item", kiri, 0.300, 0.320, x0=0.1, x1=0.45),
        _block("list_item", kanan, 0.290, 0.310, x0=0.55, x1=0.9),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert merged[0].text == f"{kiri}\n{kanan}"


def test_far_apart_blocks_do_not_merge():
    blocks = [
        _block("paragraph", "Nama : Rina Kartika", 0.10, 0.115),
        _block("paragraph", "2. HARGA", 0.40, 0.415),  # jarak jauh -> section baru
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 2


def test_different_families_never_merge_even_if_close():
    blocks = [
        _block("paragraph", "Rincian sebagai berikut:", 0.10, 0.115),
        _block("table", "| No | Uraian |", 0.117, 0.30),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 2
    assert merged[1].type == "table"


def test_title_and_section_header_share_a_family():
    blocks = [
        _block("title", "SURAT PERINTAH KERJA", 0.05, 0.065),
        _block("section_header", "Nomor : 123/ABC11/ABC-SET/2026", 0.067, 0.08),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].type == "title"


def test_solo_block_keeps_itself_as_only_atomic_member():
    blocks = [_block("paragraph", "Berdasarkan hasil negosiasi harga...", 0.1, 0.13)]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 1
    assert merged[0].atomic == [merged[0]]


def test_union_bbox_covers_all_merged_members():
    blocks = [
        _block("paragraph", "Alamat : Kampus Universitas Contoh", 0.20, 0.215, x0=0.15, x1=0.60),
        _block("paragraph", "Jl. Merpati Raya No. 18, Semarang", 0.217, 0.232, x0=0.15, x1=0.55),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 1
    xmin, ymin, xmax, ymax = merged[0].bbox
    assert xmin == 0.15 and ymin == 0.20 and xmax == 0.60 and ymax == 0.232


def test_attestation_detected_right_after_standalone_pihak_heading():
    """
    Pola nyata: nama+jabatan penandatangan tercetak POLOS (tanpa label 'Nama:'/'Jabatan:')
    tepat di bawah judul berdiri sendiri 'PIHAK PERTAMA' -- bukan di preamble yang berlabel.
    """
    blocks = [
        _block("section_header", "PIHAK PERTAMA", 0.75, 0.765),
        _block("paragraph", "Bayu Pratama\nDirektur Utama", 0.80, 0.83),
    ]
    annotated = annotate_attestation(blocks)
    assert annotated[1].type == "attestation"


def test_attestation_detection_skips_signature_image_in_between():
    """Antara judul 'PIHAK KEDUA' dan nama penandatangan biasanya ada foto meterai/ttd."""
    blocks = [
        _block("section_header", "PIHAK KEDUA", 0.75, 0.765),
        _block("image", "", 0.77, 0.80),
        _block("paragraph", "Sari Wulandari\nDirektur", 0.82, 0.85),
    ]
    annotated = annotate_attestation(blocks)
    assert annotated[2].type == "attestation"
    assert annotated[1].type == "image"  # gambar tidak ikut berubah


def test_labeled_preamble_block_is_not_misidentified_as_attestation():
    """
    Regresi: blok 'Nama/Jabatan/Alamat' BERLABEL di preamble (bukan tanda tangan) sempat
    salah tertandai attestation ketika detektor lama menghitung label, bukan posisi.
    """
    blocks = [
        _block(
            "key_value",
            "Nama\nJabatan\n: Budi Santoso Wijaya\n: Direktur Operasional",
            0.30,
            0.34,
        )
    ]
    annotated = annotate_attestation(blocks)
    assert annotated[0].type == "key_value"


def test_heading_text_that_merely_starts_with_pihak_does_not_count():
    """'PIHAK PERTAMA memberi perintah kerja...' bukan judul berdiri sendiri -- dilarang cocok."""
    blocks = [
        _block("list_item", "PIHAK PERTAMA memberi perintah kerja Pengadaan...", 0.45, 0.47),
        _block("paragraph", "PIHAK KEDUA melaksanakan Pengadaan...", 0.48, 0.50),
    ]
    annotated = annotate_attestation(blocks)
    assert annotated[1].type == "paragraph"


def test_narrative_paragraph_does_not_merge_with_bare_form_label():
    """
    Regresi utama: paragraf narasi panjang dulu tergabung dengan blok label form
    berdekatan ('Nama'/'Jabatan' tanpa titik dua dari kolom OCR terpisah), menghasilkan
    satu blok raksasa yang salah ditandai attestation. Sekarang beda keluarga (tipe beda).
    """
    blocks = [
        _block(
            "paragraph",
            "Berdasarkan hasil negosiasi harga pada tanggal 29 Mei 2026, tentang Pengadaan...",
            0.10,
            0.115,
        ),
        _block("key_value", "Nama", 0.117, 0.13),
        _block("key_value", "Jabatan", 0.132, 0.145),
        _block("key_value", ": Budi Santoso Wijaya", 0.117, 0.13, x0=0.5),
        _block("key_value", ": Direktur Operasional", 0.132, 0.145, x0=0.5),
    ]
    merged = merge_adjacent_blocks(blocks)
    assert len(merged) == 2  # paragraf tetap sendiri; 4 baris label/nilai tergabung terpisah
    assert merged[0].type == "paragraph" and "negosiasi" in merged[0].text
    assert merged[1].type == "key_value" and len(merged[1].atomic) == 4


def test_full_pipeline_on_realistic_page_fragment():
    """Simulasi satu halaman: judul, paragraf, blok tanda tangan pihak (posisional), tabel."""
    blocks = [
        _block("title", "SURAT PERINTAH KERJA", 0.05, 0.065),
        _block("section_header", "Nomor : 045/SPK/2031", 0.067, 0.08),
        _block("paragraph", "Berdasarkan hasil negosiasi...", 0.10, 0.13),
        _block("section_header", "PIHAK KEDUA", 0.75, 0.765),
        _block("paragraph", "Bayu Pratama\nDirektur Utama", 0.80, 0.83),
        _block("table", "| No | Uraian |", 0.90, 0.98),
    ]
    result = process_page_blocks(blocks)
    types = [b.type for b in result]
    assert types == ["title", "paragraph", "section_header", "attestation", "table"]


# ---------------------------------------------------------------- reading_order_sort
def test_two_column_signature_blocks_reordered_column_major():
    """
    Regresi urutan tampil: blok kanan (PIHAK KEDUA) yang union-bbox-nya memanjang ke
    bawah (mencakup gambar meterai) dulu ter-selip sebelum kolom kiri selesai kalau
    cuma diurutkan ymin murni. Sekarang harus rapi: kolom kiri lengkap dulu, baru kanan.
    """
    left_heading = _block("section_header", "PIHAK PERTAMA", 0.712, 0.725, x0=0.138, x1=0.266)
    right_image = _block("image", "", 0.713, 0.834, x0=0.650, x1=0.816)
    right_heading = _block("paragraph", "PIHAK KEDUA", 0.713, 0.725, x0=0.649, x1=0.752)
    left_image = _block("image", "", 0.721, 0.834, x0=0.093, x1=0.365)
    left_attestation = _block(
        "attestation", "Budi Santoso Wijaya\nDirektur", 0.764, 0.833, x0=0.111, x1=0.365
    )
    right_attestation = _block(
        "attestation", "Sari Wulandari\nDirektur", 0.803, 0.834, x0=0.649, x1=0.814
    )

    # Urutan input sengaja diacak (mensimulasikan ymin murni yang menyelip-nyelipkan kolom)
    shuffled = [
        left_heading,
        right_image,
        right_heading,
        left_image,
        left_attestation,
        right_attestation,
    ]
    result = reading_order_sort(shuffled)

    left_idx = [result.index(b) for b in (left_heading, left_image, left_attestation)]
    right_idx = [result.index(b) for b in (right_heading, right_image, right_attestation)]
    assert max(left_idx) < min(right_idx), (
        "seluruh kolom kiri harus selesai sebelum kolom kanan dimulai"
    )


def test_two_column_reorder_ignores_unrelated_content_right_before_it():
    """
    Regresi nyata: paragraf penutup + blok lokasi/tanggal tepat SEBELUM area tanda
    tangan (posisi/x-center di tengah halaman) sempat ikut "mencemari" deteksi kolom
    berbasis celah horizontal umum, membuat seluruh area tanda tangan gagal terurut
    ulang dan balik ke urutan-Y murni yang salah. Konten sebelum judul "PIHAK X"
    seharusnya tidak pernah memengaruhi reorder-nya sama sekali.
    """
    closing = _block(
        "paragraph", "Demikian Surat Perintah ini dibuat...", 0.650, 0.665, x0=0.14, x1=0.85
    )
    lokasi = _block("key_value", ": Bandung", 0.673, 0.681, x0=0.25, x1=0.33)
    tanggal_label = _block("paragraph", "Tanggal", 0.689, 0.697, x0=0.14, x1=0.20)
    tanggal_val = _block("key_value", ": 2 Juni 2026", 0.6885, 0.6945, x0=0.25, x1=0.345)
    left_heading = _block("section_header", "PIHAK PERTAMA", 0.7125, 0.7255, x0=0.138, x1=0.266)
    right_heading = _block("paragraph", "PIHAK KEDUA", 0.7135, 0.7245, x0=0.649, x1=0.752)
    right_image = _block("image", "", 0.713, 0.834, x0=0.650, x1=0.816)
    left_image = _block("image", "", 0.721, 0.834, x0=0.093, x1=0.365)
    left_attestation = _block(
        "attestation", "Budi Santoso Wijaya\nDirektur", 0.764, 0.833, x0=0.111, x1=0.365
    )
    right_attestation = _block(
        "attestation", "Sari Wulandari\nDirektur", 0.803, 0.834, x0=0.649, x1=0.814
    )

    preamble = [closing, lokasi, tanggal_val, tanggal_label]
    result = reading_order_sort(
        [
            closing,
            lokasi,
            tanggal_val,
            tanggal_label,
            left_heading,
            right_image,
            right_heading,
            left_image,
            left_attestation,
            right_attestation,
        ]
    )

    expected_preamble_order = sorted(preamble, key=lambda b: (b.bbox[1] + b.bbox[3]) / 2)
    assert result[:4] == expected_preamble_order, (
        "konten sebelum blok tanda tangan tidak boleh ikut tersusun ulang"
    )
    left_idx = [result.index(b) for b in (left_heading, left_image, left_attestation)]
    right_idx = [result.index(b) for b in (right_heading, right_image, right_attestation)]
    assert max(left_idx) < min(right_idx)


def test_single_column_flow_order_is_preserved():
    """Alur satu kolom biasa (lebar hampir penuh halaman) tidak boleh terurut ulang."""
    blocks = [
        _block("section_header", "1. LINGKUP PEKERJAAN", 0.10, 0.12, x0=0.14, x1=0.85),
        _block(
            "list_item", "PIHAK PERTAMA memberi perintah kerja...", 0.13, 0.18, x0=0.16, x1=0.85
        ),
        _block("section_header", "2. HARGA", 0.20, 0.22, x0=0.14, x1=0.85),
        _block("paragraph", "Jumlah harga untuk Pengadaan...", 0.23, 0.26, x0=0.14, x1=0.85),
    ]
    result = reading_order_sort(list(reversed(blocks)))  # input sengaja dibalik urutannya
    assert [b.text for b in result] == [b.text for b in blocks]


def test_continuation_line_attaches_to_nearest_pair_not_first_processed():
    """
    Regresi bug nyata (SPK lisensi perangkat lunak, halaman 1): baris sambungan alamat
    "JI. Merpati Raya No. 18..." berjarak-Y 0.030 dari nilai "Jabatan" dan 0.016 dari nilai
    "Alamat" -- keduanya di bawah ambang CONTINUATION_MAX_GAP (0.03), tapi Alamat jelas
    lebih dekat. Versi lama mencocokkan berurutan sesuai urutan pasangan terbentuk (Nama,
    Jabatan, Alamat) dan Jabatan diproses lebih dulu, sehingga merebutnya secara serakah --
    ini bukan hanya salah menempel, tapi juga membuat Jabatan "tertarik" ke bawah Alamat
    dalam urutan baca akhir. Baris sambungan harus menempel ke pasangan TERSEKAT (Alamat)
    supaya urutannya tetap Nama, Jabatan, Alamat sesuai barisan dokumen aslinya.
    """
    nama_label = _block("key_value", "Nama", 0.180, 0.190, x0=0.5, x1=0.6)
    nama_value = _block("key_value", ": Budi Santoso Wijaya", 0.185, 0.195, x0=0.6, x1=0.9)
    jabatan_label = _block("key_value", "Jabatan", 0.2007, 0.2107, x0=0.5, x1=0.6)
    jabatan_value = _block("key_value", ": Direktur Operasional", 0.2057, 0.2157, x0=0.6, x1=0.9)
    alamat_label = _block("key_value", "Alamat", 0.2147, 0.2247, x0=0.5, x1=0.6)
    alamat_value = _block(
        "key_value", ": Kampus Universitas Contoh", 0.2197, 0.2297, x0=0.6, x1=0.9
    )
    continuation = _block(
        "paragraph",
        "JI. Merpati Raya No. 18 Banyumanik, Semarang",
        0.2357,
        0.2457,
        x0=0.6,
        x1=0.9,
    )

    blocks = [
        nama_label,
        nama_value,
        jabatan_label,
        jabatan_value,
        alamat_label,
        alamat_value,
        continuation,
    ]
    result = pair_labels_with_values(blocks)

    assert [b.text for b in result] == [
        "Nama : Budi Santoso Wijaya",
        "Jabatan : Direktur Operasional",
        "Alamat : Kampus Universitas Contoh JI. Merpati Raya No. 18 Banyumanik, Semarang",
    ], (
        "urutan & isi harus sesuai barisan dokumen asli: Nama, Jabatan, lalu Alamat (dengan "
        "sambungannya)"
    )


def test_continuation_matching_ignores_section_headers():
    """
    Regresi bug nyata (SPK lisensi perangkat lunak, halaman 2): judul berdiri sendiri
    "PIHAK PERTAMA" (section_header, bukan baris sambungan) berjarak-Y dekat di bawah
    "Tanggal : 2 Juni 2026" -- filter tipe kandidat versi lama (`not in {"key_value"}`)
    keliru meloloskannya sebagai "sambungan" nilai Tanggal. Hanya baris berjenis
    "paragraph" (baris teks polos tanpa label) yang boleh dianggap sambungan.
    """
    tanggal_label = _block("key_value", "Tanggal", 0.500, 0.510, x0=0.1, x1=0.3)
    tanggal_value = _block("key_value", ": 2 Juni 2026", 0.505, 0.515, x0=0.3, x1=0.6)
    pihak_heading = _block("section_header", "PIHAK PERTAMA", 0.520, 0.530, x0=0.3, x1=0.6)

    result = pair_labels_with_values([tanggal_label, tanggal_value, pihak_heading])

    assert result[0].text == "Tanggal : 2 Juni 2026", (
        "'Tanggal : 2 Juni 2026' harus tetap satu unit, bukan terpisah"
    )
    assert result[1] is pihak_heading, (
        "'PIHAK PERTAMA' bukan sambungan Tanggal, harus tetap blok terpisah"
    )


def test_label_pairing_generalizes_to_words_outside_the_known_vocabulary():
    """
    Regresi bug nyata (SPK lisensi perangkat lunak, halaman 3): "Lampiran" dan "SPK"
    bukan kata yang ada di BARE_FORM_LABELS, jadi versi lama (berbasis daftar kata) tidak
    pernah mengenalinya sebagai label -- keduanya tetap bertipe "paragraph" polos dan
    malah tergabung lewat merge_adjacent_blocks dengan baris sambungan TETANGGA yang
    sebenarnya bukan miliknya ("SPK" + "Teknologi Informasi Universitas Contoh").
    Pemasangan sekarang berbasis bentuk & posisi (label pendek tanpa titik dua, sebaris,
    di kiri nilainya), bukan daftar kata, supaya berlaku untuk label apa pun.
    """
    # Koordinat asli dari dokumen (bukan dibuat-buat) -- diambil langsung dari output
    # Docling untuk halaman 3 SPK itu sebelum pengelompokan. Teksnya diganti rekaan.
    lampiran_label = _block("paragraph", "Lampiran", 0.05654, 0.07197, x0=0.13019, x1=0.20595)
    lampiran_value = _block(
        "key_value",
        ": Pengadaan Perpanjangan Lisensi Basis Data Tahun 2026 Kebutuhan Direktorat Pusat",
        0.05615,
        0.07038,
        x0=0.21886,
        x1=0.84848,
    )
    lampiran_continuation = _block(
        "paragraph",
        "Teknologi Informasi Universitas Contoh",
        0.07236,
        0.08541,
        x0=0.22054,
        x1=0.51235,
    )
    spk_label = _block("paragraph", "SPK", 0.0858, 0.10123, x0=0.12907, x1=0.16554)
    spk_value = _block(
        "key_value",
        ": 123/ABC11/ABC-SET/2026, tanggal 2 Juni 2026",
        0.08699,
        0.10043,
        x0=0.22166,
        x1=0.57351,
    )

    blocks = [lampiran_label, lampiran_value, lampiran_continuation, spk_label, spk_value]
    result = pair_labels_with_values(blocks)

    assert [b.text for b in result] == [
        "Lampiran : Pengadaan Perpanjangan Lisensi Basis Data Tahun 2026 Kebutuhan Direktorat "
        "Pusat Teknologi Informasi Universitas Contoh",
        "SPK : 123/ABC11/ABC-SET/2026, tanggal 2 Juni 2026",
    ], (
        "'Lampiran' dan 'SPK' harus terpasang dengan nilainya masing-masing (termasuk baris "
        "sambungannya), bukan dengan tetangga yang salah"
    )
