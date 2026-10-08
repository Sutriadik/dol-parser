"""
Open ADE — Block grouping & semantic annotation (layout awareness, layer 2).

Docling sudah memberi label layout asli per elemen (section_header, caption, list_item, dst.)
dan deteksi gambar (doc.pictures) — tapi mengubahnya jadi StructureItem satu-lawan-satu
menghasilkan puluhan blok kecil per halaman (tiap baris = satu blok), jauh lebih berisik
dibanding LandingAI yang mengelompokkan baris-baris terkait jadi satu unit semantik
(mis. blok "Nama/Jabatan/Perusahaan" jadi satu "attestation").

Modul ini murni fungsi (tanpa dependency ke Docling/pydantic) supaya bisa diuji tanpa
menjalankan OCR: input & output berupa `DraftBlock` yang dipetakan ke StructureItem oleh
pemanggilnya (docling_parser.py).

Tiga langkah (urut dipanggil oleh `process_page_blocks`):
0. `pair_labels_with_values` — pasangkan label form polos ("Nama", "Jabatan", "Tanggal", ..
   tanpa titik dua) dengan nilainya (": Budi Santoso Wijaya") berdasarkan jarak-Y
   TERDEKAT, bukan urutan mentah dari Docling. Perlu ada langkah ini karena Docling kadang
   mengeluarkan seluruh kolom label dulu baru seluruh kolom nilai untuk layout 2 kolom
   semacam ini -- dan urutannya bisa tidak konsisten (label ketiga "Alamat" bisa muncul
   PALING TERAKHIR walau posisinya di atas baris lain), jadi urutan baca tidak bisa
   dipakai untuk menentukan pasangannya, hanya posisi fisik (Y) yang bisa diandalkan.
1. `merge_adjacent_blocks` — gabungkan baris-baris berdekatan yang senada (judul+nomornya,
   atau paragraf/key-value/list yang berurutan tanpa jeda vertikal besar) jadi satu blok,
   dengan `atomic` menyimpan baris aslinya (setara `atomic_grounding` LandingAI).
2. `annotate_attestation` — tandai blok tepat setelah judul berdiri sendiri "PIHAK
   PERTAMA/KEDUA/KESATU" jadi tipe "attestation" (posisi, bukan isi teks -- nama+jabatan
   penandatangan asli biasanya polos TANPA label "Nama:"/"Jabatan:", beda dari blok
   Nama/Jabatan/Alamat berlabel di preamble yang bukan tanda tangan).
"""

import re
from dataclasses import dataclass, field

BBox = tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax), ternormalisasi 0..1

# Kata label form Indonesia yang sering tercetak POLOS (tanpa titik dua) di kolom
# terpisah dari nilainya -- dipakai baik oleh docling_parser.py (untuk mengklasifikasi
# item ini sebagai "key_value" walau tak ada ":") maupun pair_labels_with_values di
# bawah (untuk tahu item mana yang harus dicari pasangan nilainya).
BARE_FORM_LABELS: set[str] = {
    "nama",
    "jabatan",
    "alamat",
    "perusahaan",
    "npwp",
    "tanggal",
    "lokasi",
    "dibuat di",
}

# Baris hanya digabung dengan baris yang PERSIS setipe (kecuali title+section_header).
# Keluarga campuran {paragraph, key_value, list_item} sempat dicoba dan menggabungkan paragraf
# narasi dengan label form di bawahnya: dua pihak tercampur dalam satu blok. Same-type-only
# tetap menyatukan alamat dua baris dan tumpukan Nama/Jabatan/Alamat, tanpa menjembatani
# prosa dan label form.
MERGE_FAMILIES: list[set] = [
    {"title", "section_header"},
    {"paragraph"},
    {"key_value"},
    {"list_item"},
]

# Jarak vertikal maksimum (fraksi tinggi halaman) antar-blok supaya masih dianggap
# "berdekatan" -- kira-kira satu baris kosong. Di atas ini dianggap section baru.
MAX_VERTICAL_GAP = 0.018

# Pola blok tanda tangan Indonesia yang SEBENARNYA: nama+jabatan penandatangan tercetak
# polos (TANPA label "Nama:"/"Jabatan:") tepat di bawah judul berdiri sendiri "PIHAK
# PERTAMA"/"KEDUA"/"KESATU". Ini beda dari blok Nama/Jabatan/Alamat di preamble (yang
# memang berlabel, dan BUKAN tanda tangan) -- makanya deteksi dulu sempat salah sasaran.
_PIHAK_HEADING = re.compile(r"(?i)^\s*pihak\s+(?:pertama|kedua|kesatu|ke[\s-]?1|ke[\s-]?2)\s*$")
ATTESTATION_LOOKAHEAD = 3  # lompati gambar (foto meterai/ttd) untuk cari blok teks berikutnya
ATTESTATION_MAX_CHARS = 400  # jangan tandai blok yang jelas kepanjangan untuk sekadar nama+jabatan


@dataclass
class DraftBlock:
    """Representasi sementara satu blok sebelum jadi StructureItem."""

    type: str
    text: str
    bbox: BBox | None
    confidence: float
    start: int | None = None  # posisi karakter di markdown (untuk range gabungan)
    end: int | None = None
    atomic: list["DraftBlock"] = field(default_factory=list)  # baris asli jika hasil gabungan

    def _family(self) -> set | None:
        return next((fam for fam in MERGE_FAMILIES if self.type in fam), None)


def _vertical_gap(prev: DraftBlock, curr: DraftBlock) -> float:
    if not prev.bbox or not curr.bbox:
        return float("inf")
    return curr.bbox[1] - prev.bbox[3]  # curr.ymin - prev.ymax


def _union_bbox(boxes: list[BBox]) -> BBox | None:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


# Potongan yang boleh disusun ulang per baris visual: pendek (label/nilai), bukan baris
# prosa. Prosa dua kolom yang sejajar tidak boleh diselang-seling baris per baris.
ROW_FRAGMENT_MAX_CHARS = 40
# Dua potongan dianggap satu angka yang terpotong OCR bila celah horizontalnya lebih
# kecil dari ini (fraksi lebar halaman) -- "52,43" + "7" + ",916" hampir bersentuhan.
NUMBER_JOIN_MAX_GAP = 0.004
_NUMERIC_FRAGMENT = re.compile(r"^[\d.,]+$")


def _same_row(a: DraftBlock, b: DraftBlock) -> bool:
    """Tumpang-tindih vertikal lebih dari separuh tinggi potongan yang lebih pendek."""
    overlap = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    shorter = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    return shorter > 0 and overlap > shorter / 2


def _join_row(row: list[DraftBlock]) -> str:
    """Satu baris visual kiri->kanan. Potongan angka yang bersentuhan disambung tanpa
    spasi ("52,43"+"7"+",916" -> "52,437,916"); potongan lain dipisah satu spasi."""
    text = row[0].text.strip()
    for prev, cur in zip(row, row[1:]):
        piece = cur.text.strip()
        touching = cur.bbox[0] - prev.bbox[2] <= NUMBER_JOIN_MAX_GAP
        numeric = _NUMERIC_FRAGMENT.match(prev.text.strip()) and _NUMERIC_FRAGMENT.match(piece)
        text += piece if (touching and numeric) else " " + piece
    return text


def _group_text(members: list[DraftBlock]) -> str:
    """
    Teks gabungan satu grup. Normalnya urutan mentah Docling, satu potongan per baris.

    Pengecualian: blok ringkasan harga dua kolom (label kiri, nilai kanan) yang OCR-nya
    terpecah. Docling bisa mengeluarkan potongannya melompat-lompat -- SPH PT Vendor A
    menghasilkan "PPN 11%\\n52,43\\nTotal Beban Pekerjaan\\n7\\n,916\\n533,218,400", lalu LLM
    menyambung digitnya jadi PPN 5.243.916.533. Kalau urutan mentahnya MUNDUR secara
    vertikal dan semua potongannya pendek, potongan disusun ulang per baris visual.
    """
    texts = [m for m in members if m.text]
    boxed = all(m.bbox for m in texts)
    jumps_back = boxed and any(_y_center(b) < a.bbox[1] for a, b in zip(texts, texts[1:]))
    short = all(len(m.text.strip()) <= ROW_FRAGMENT_MAX_CHARS for m in texts)
    if not (jumps_back and short):
        return "\n".join(m.text for m in texts)

    rows: list[list[DraftBlock]] = []
    for m in sorted(texts, key=_y_center):
        row = next((r for r in rows if _same_row(r[0], m)), None)
        if row is None:
            rows.append([m])
        else:
            row.append(m)
    return "\n".join(_join_row(sorted(r, key=_x_left)) for r in rows)


def _satu_kolom(members: list[DraftBlock]) -> bool:
    """Semua anggota tumpang-tindih secara horizontal (tidak ada kolom kiri/kanan)."""
    boxes = [m.bbox for m in members]
    return all(boxes) and max(b[0] for b in boxes) < min(b[2] for b in boxes)


def _merge_group(members: list[DraftBlock]) -> DraftBlock:
    if members[0].type == "list_item" and len(members) > 1 and _satu_kolom(members):
        # Docling kadang mengeluarkan butir list tidak berurutan (kontrak Pasal 13: "b."
        # sebelum "a."). Di satu kolom, atas-ke-bawah adalah urutan baca yang benar.
        members = sorted(members, key=lambda m: m.bbox[1])
    if len(members) == 1:
        solo = members[0]
        solo.atomic = [solo] if solo.atomic == [] else solo.atomic
        return solo
    boxes = [m.bbox for m in members if m.bbox]
    starts = [m.start for m in members if m.start is not None]
    ends = [m.end for m in members if m.end is not None]
    return DraftBlock(
        type=members[0].type,
        text=_group_text(members),
        bbox=_union_bbox(boxes),
        confidence=min(m.confidence for m in members),
        start=min(starts) if starts else None,
        end=max(ends) if ends else None,
        atomic=list(members),
    )


def _is_pihak_heading(block: DraftBlock) -> bool:
    return bool(_PIHAK_HEADING.match(block.text or ""))


def _y_center(block: DraftBlock) -> float:
    return (block.bbox[1] + block.bbox[3]) / 2 if block.bbox else 0.0


def _x_center(block: DraftBlock) -> float:
    return (block.bbox[0] + block.bbox[2]) / 2 if block.bbox else 0.5


def _x_left(block: DraftBlock) -> float:
    return block.bbox[0] if block.bbox else 0.0


# Label kolom kiri di layout dua kolom dikenali dari bentuk & posisinya, bukan daftar kata
# tetap, supaya berlaku juga untuk label yang belum pernah terlihat. BARE_FORM_LABELS di
# docling_parser.py hanya penjagaan tambahan.
LABEL_MAX_WORDS = 4
LABEL_MAX_CHARS = 40
# Label & nilainya di layout ini SEBARIS (sisi-kiri vs sisi-kanan) -- toleransi jarak-Y
# harus ketat supaya tidak salah pasang ke baris lain yang kebetulan pendek juga.
LABEL_VALUE_MAX_Y_GAP = 0.02


def _looks_like_label_text(text: str) -> bool:
    """Ciri STRUKTURAL baris label kolom-kiri: pendek, tanpa titik dua sama sekali,
    tanpa tanda baca akhir kalimat -- bukan dicocokkan ke kata tertentu, supaya kata
    label apa pun (bukan cuma "Nama"/"Jabatan"/dkk.) ikut dikenali."""
    t = (text or "").strip()
    if not t or ":" in t or "\n" in t:
        return False
    if t[-1] in ".,;":
        return False
    if len(t) > LABEL_MAX_CHARS:
        return False
    return len(t.split()) <= LABEL_MAX_WORDS


def _is_bare_label(block: DraftBlock) -> bool:
    return (
        block.bbox is not None
        and block.type not in {"table", "image", "logo"}
        and not _is_pihak_heading(block)
        and _looks_like_label_text(block.text or "")
    )


def _is_value_line(block: DraftBlock) -> bool:
    return block.type == "key_value" and block.text.strip().startswith(":")


# Toleransi jarak-Y untuk mencari baris sambungan (baris ke-2 alamat yang biasanya
# tanpa titik dua sama sekali, mis. "Jl. Telekomunikasi No. 1...") tepat di bawah nilai
# yang sudah cocok. Sedikit lebih lega dari MAX_VERTICAL_GAP karena baris sambungan
# kadang berjarak agak lebih jauh dari nilainya sendiri.
CONTINUATION_MAX_GAP = 0.03
# Baris sambungan dicocokkan lewat tepi kiri, bukan titik tengah: tepi kiri nilai dan
# sambungannya rata ke kolom yang sama walau panjang teksnya jauh berbeda.
CONTINUATION_MAX_X_DRIFT = 0.05


def _merge_label_value(
    label: DraftBlock, value: DraftBlock, continuation: DraftBlock | None
) -> DraftBlock:
    """'Nama' + ': Budi Santoso Wijaya' -> 'Nama : Budi Santoso Wijaya' (satu spasi,
    bukan baris baru -- nilainya sudah membawa titik dua sendiri)."""
    members = [label, value] + ([continuation] if continuation else [])
    text = " ".join(m.text.strip() for m in members if m.text)
    boxes = [m.bbox for m in members if m.bbox]
    starts = [m.start for m in members if m.start is not None]
    ends = [m.end for m in members if m.end is not None]
    return DraftBlock(
        type="key_value",
        text=text,
        bbox=_union_bbox(boxes),
        confidence=min(m.confidence for m in members),
        start=min(starts) if starts else None,
        end=max(ends) if ends else None,
        atomic=members,
    )


def pair_labels_with_values(blocks: list[DraftBlock]) -> list[DraftBlock]:
    """
    Pasangkan tiap label form polos ("Nama", "Tanggal", dst.) dengan NILAI ber-jarak-Y
    TERDEKAT yang belum dipakai -- bukan dengan nilai yang kebetulan muncul berikutnya
    di urutan mentah Docling (lihat catatan modul di atas kenapa urutan mentah tak bisa
    dipercaya di sini). Baris sambungan tanpa label/nilai (mis. alamat baris ke-2) ikut
    ditempelkan ke pasangan yang posisinya tepat di bawah & sekolom dengannya.

    Tidak melakukan apa pun kalau halaman ini tidak punya label form sama sekali --
    no-op murni untuk halaman biasa (paragraf/tabel).
    """
    values = [b for b in blocks if _is_value_line(b)]
    if not values:
        return blocks
    value_ids = {id(v) for v in values}
    labels = [b for b in blocks if id(b) not in value_ids and _is_bare_label(b)]
    if not labels:
        return blocks

    # Kumpulkan semua pasangan (label, nilai) yang sebaris dengan label di kiri nilainya, lalu
    # urutkan dari yang terdekat, supaya label pertama tidak serakah merebut nilai label tetangga.
    triples: list[tuple[float, DraftBlock, DraftBlock]] = []
    for label in labels:
        for value in values:
            if not (label.bbox and value.bbox):
                continue
            if _x_center(label) >= _x_center(value):
                continue
            gap = abs(_y_center(label) - _y_center(value))
            if gap > LABEL_VALUE_MAX_Y_GAP:
                continue
            triples.append((gap, label, value))
    triples.sort(key=lambda t: t[0])

    used_label_ids: set[int] = set()
    used_value_ids: set[int] = set()
    pairs: list[tuple[DraftBlock, DraftBlock]] = []
    for _, label, value in triples:
        if id(label) in used_label_ids or id(value) in used_value_ids:
            continue
        used_label_ids.add(id(label))
        used_value_ids.add(id(value))
        pairs.append((label, value))

    if not pairs:
        return blocks
    pairs.sort(key=lambda pair: _y_center(pair[0]))

    consumed_ids = {id(b) for pair in pairs for b in pair}

    # Sama seperti pasangan label-nilai di atas: kumpulkan semua kombinasi (kandidat, pasangan)
    # lalu urutkan berdasar jarak-Y, supaya pasangan yang diproses lebih dulu tidak merebut
    # baris sambungan milik pasangan tetangga.
    candidates = [
        cand
        for cand in blocks
        if id(cand) not in consumed_ids and cand.bbox and cand.type == "paragraph"
    ]
    triples: list[tuple[float, DraftBlock, DraftBlock]] = []
    for cand in candidates:
        for _, value in pairs:
            if not value.bbox:
                continue
            gap = _y_center(cand) - _y_center(value)
            if not (0 <= gap <= CONTINUATION_MAX_GAP):
                continue
            # Sejajar TEPI KIRI (bukan titik-tengah) dengan nilainya -- baris sambungan
            # rata kiri ke kolom yang sama, tapi panjang tekesnya beda-beda (nilai baris
            # pertama bisa jauh lebih panjang dari sambungannya), jadi titik-tengah kedua
            # baris bisa jauh melenceng walau keduanya sama-sama mulai dari kolom yang sama.
            if abs(_x_left(cand) - _x_left(value)) > CONTINUATION_MAX_X_DRIFT:
                continue
            triples.append((gap, cand, value))
    triples.sort(key=lambda t: t[0])

    continuation_for_value: dict[int, DraftBlock] = {}
    claimed_candidate_ids: set[int] = set()
    for _, cand, value in triples:
        if id(cand) in claimed_candidate_ids or id(value) in continuation_for_value:
            continue
        continuation_for_value[id(value)] = cand
        claimed_candidate_ids.add(id(cand))

    consumed_ids |= claimed_candidate_ids
    merged_units: list[DraftBlock] = []
    for label, value in pairs:
        continuation = continuation_for_value.get(id(value))
        merged_units.append(_merge_label_value(label, value, continuation))

    remaining = [b for b in blocks if id(b) not in consumed_ids]
    return sorted(merged_units + remaining, key=_y_center)


def merge_adjacent_blocks(blocks: list[DraftBlock]) -> list[DraftBlock]:
    """
    Gabungkan blok berurutan (urutan sudah reading-order) yang: tipe satu keluarga,
    berjarak vertikal dekat, dan sama-sama punya bbox (tabel/gambar tanpa bbox jelas
    tidak pernah masuk sini karena tipenya memang di luar MERGE_FAMILIES).

    Judul "PIHAK PERTAMA/KEDUA/KESATU" TIDAK PERNAH ikut digabung ke arah mana pun --
    di layout dua kolom (blok tanda tangan kiri/kanan), Docling kadang salah memberi
    label judul ini sebagai "text" generik (bukan section_header), sehingga tanpa
    pengecualian ini ia bisa "termakan" jadi baris terakhir blok tanda tangan pihak
    SEBELUMNYA -- persis yang terjadi pada PIHAK KEDUA saat pertama kali diuji.
    """
    if not blocks:
        return []
    merged: list[list[DraftBlock]] = [[blocks[0]]]
    for block in blocks[1:]:
        current_group = merged[-1]
        last = current_group[-1]
        same_family = last._family() is not None and last._family() == block._family()
        ref = last
        if block.type == "list_item" and all(m.bbox for m in current_group):
            # Butir yang datang tidak berurutan ("b." lalu "a.") membuat anggota terakhir
            # bukan yang terbawah; ukur jarak dari dasar grup supaya "c." tidak terlepas.
            ref = max(current_group, key=lambda m: m.bbox[3])
        close_enough = _vertical_gap(ref, block) <= MAX_VERTICAL_GAP
        blocks_heading = _is_pihak_heading(last) or _is_pihak_heading(block)
        if same_family and close_enough and not blocks_heading:
            current_group.append(block)
        else:
            merged.append([block])
    return [_merge_group(g) for g in merged]


def annotate_attestation(blocks: list[DraftBlock]) -> list[DraftBlock]:
    """
    Tandai blok TEKS PERTAMA setelah judul berdiri sendiri "PIHAK PERTAMA/KEDUA/KESATU"
    sebagai "attestation" -- itulah nama+jabatan penandatangan di dunia nyata (lihat
    catatan di atas). Melompati blok gambar (foto meterai/tanda tangan) di antaranya.

    Pemicunya dicek dari TEKS saja, bukan disyaratkan bertipe title/section_header --
    Docling tidak selalu konsisten memberi label section_header untuk judul pendek
    seperti ini (lihat catatan `merge_adjacent_blocks`). Murni relabeling -- tidak
    mengubah teks/bbox, jadi tidak berisiko ke grounding.
    """
    for i, block in enumerate(blocks):
        if not _is_pihak_heading(block):
            continue
        for candidate in blocks[i + 1 : i + 1 + ATTESTATION_LOOKAHEAD]:
            if candidate.type in {"image", "logo"}:
                continue
            if (
                candidate.type in {"paragraph", "key_value"}
                and len(candidate.text) <= ATTESTATION_MAX_CHARS
            ):
                candidate.type = "attestation"
            break
    return blocks


# Jarak titik-tengah-Y maksimum supaya dua judul "PIHAK X" dianggap sejajar (satu
# baris visual, blok tanda tangan berdampingan) alih-alih dua bagian berbeda.
ROW_CHAIN_TOLERANCE = 0.07
# Seberapa jauh ke bawah (fraksi tinggi halaman) dari judul "PIHAK X" masih dianggap
# bagian dari blok tanda tangannya (gambar meterai + nama/jabatan penandatangan).
SIGNATURE_FOLLOWER_WINDOW = 0.20
# Tipe blok yang boleh ikut dikumpulkan sebagai "pengikut" satu kolom tanda tangan.
_SIGNATURE_FOLLOWER_TYPES = {"image", "logo", "attestation"}


def _reorder_pihak_pair(blocks: list[DraftBlock], i: int) -> int:
    """
    blocks[i] & blocks[i+1] adalah 2 judul "PIHAK X" sejajar (blok tanda tangan
    berdampingan). Kumpulkan pengikut masing-masing MAJU dari situ saja (gambar
    meterai/attestation berikutnya, dipilih berdasarkan mana yang x-nya lebih dekat)
    dan susun ulang: seluruh kolom kiri utuh, baru seluruh kolom kanan -- bukan
    diselang-seling oleh urutan Y murni. Sengaja tidak pernah melihat ke belakang
    (blok SEBELUM judul, seperti paragraf penutup atau lokasi/tanggal di atasnya,
    tidak pernah ikut tersentuh oleh reorder ini).

    Return indeks setelah segmen yang baru disusun ulang.
    """
    left, right = (
        (blocks[i], blocks[i + 1])
        if _x_center(blocks[i]) < _x_center(blocks[i + 1])
        else (blocks[i + 1], blocks[i])
    )
    j = i + 2
    left_tail: list[DraftBlock] = []
    right_tail: list[DraftBlock] = []
    while j < len(blocks):
        cand = blocks[j]
        if cand.type not in _SIGNATURE_FOLLOWER_TYPES or not cand.bbox:
            break
        if _y_center(cand) - _y_center(left) > SIGNATURE_FOLLOWER_WINDOW:
            break
        dist_left = abs(_x_center(cand) - _x_center(left))
        dist_right = abs(_x_center(cand) - _x_center(right))
        (left_tail if dist_left <= dist_right else right_tail).append(cand)
        j += 1
    blocks[i:j] = [left, *left_tail, right, *right_tail]
    return j


def reading_order_sort(blocks: list[DraftBlock]) -> list[DraftBlock]:
    """
    Urutkan blok ala urutan baca manusia, bukan sekadar ymin menaik.

    Ymin murni gagal untuk layout dua kolom: blok tanda tangan PIHAK KEDUA (kanan)
    yang union-bbox-nya memanjang ke bawah (mencakup gambar meterai) bisa punya ymin
    lebih kecil dari judul "PIHAK PERTAMA" (kiri) sendiri, sehingga terselip sebelum
    kolom kiri selesai tanpa alasan yang masuk akal secara visual.

    Sempat dicoba deteksi kolom umum (kelompokkan blok berdekatan, cari celah-X
    terbesar) -- tapi paragraf penutup & blok lokasi/tanggal tepat di atas area
    tanda tangan ikut ter-rantai ke gerombol yang sama (jaraknya berdekatan) dan
    x-center-nya yang tersebar di tengah mengaburkan celah kolom yang sebenarnya.
    Sekarang ditarget: cuma bereaksi saat menemukan SEPASANG judul "PIHAK X" yang
    sejajar, dan hanya menyusun ulang pengikutnya (maju, tidak pernah mundur) --
    jauh lebih aman karena tidak bergantung pada isi di luar blok tanda tangan itu
    sendiri. Untuk halaman tanpa pasangan "PIHAK X" sejajar, ini no-op murni.
    """
    ordered = sorted(blocks, key=_y_center)
    i = 0
    while i < len(ordered) - 1:
        a, b = ordered[i], ordered[i + 1]
        if (
            _is_pihak_heading(a)
            and _is_pihak_heading(b)
            and a.bbox
            and b.bbox
            and abs(_y_center(a) - _y_center(b)) <= ROW_CHAIN_TOLERANCE
        ):
            i = _reorder_pihak_pair(ordered, i)
        else:
            i += 1
    return ordered


def process_page_blocks(blocks: list[DraftBlock]) -> list[DraftBlock]:
    """Pipeline lengkap: pasangkan label/nilai form, gabung baris senada berdekatan, tandai
    attestation, urutkan baca.
    """
    paired = pair_labels_with_values(blocks)
    return reading_order_sort(annotate_attestation(merge_adjacent_blocks(paired)))
