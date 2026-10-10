"""
Open ADE — Table Extractor Module
Mengekstrak seluruh tabel Markdown menjadi representasi JSON terstruktur
dengan menjaga 100% redaksi/konten sel utuh tanpa pemotongan.
"""

import re
from typing import Any

from app.extractors.deterministic.numbers import (  # noqa: F401 (re-export)
    parse_id_number,
    parse_indonesian_number,
)


def is_markdown_table_separator(line: str) -> bool:
    """Mengecek apakah suatu baris adalah separator markdown table (|---|---|)."""
    stripped = line.strip()
    if not (stripped.startswith("|") and stripped.endswith("|")):
        return False
    # Cek apakah berisi setidaknya satu '-' dan tidak ada karakter alfanumerik
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return all(re.match(r"^:?-+:?$", c) for c in cells if c)


def parse_markdown_table_row(line: str) -> list[str]:
    """Mengurai satu baris tabel markdown menjadi list sel teks utuh."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def extract_tables_from_markdown(markdown_text: str) -> list[dict[str, Any]]:
    """
    Mengekstrak seluruh tabel dari teks Markdown menjadi list dictionary terstruktur.

    Returns:
        List of dicts: [
            {
                "table_index": 1,
                "headers": ["No", "Uraian Pekerjaan", "Volume", "Satuan", "Harga
                Satuan", "Total Harga"],
                "row_count": 15,
                "rows": [
                    {
                        "No": "1",
                        "Uraian Pekerjaan": "Sewa Router Koneksi Jaringan...",
                        ...
                    }
                ],
                "raw_markdown": "| No | ... |"
            }
        ]
    """
    tables: list[dict[str, Any]] = []
    lines = markdown_text.splitlines()

    in_table = False
    current_table_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            current_table_lines.append(stripped)
        else:
            if in_table and current_table_lines:
                # Process finished table block
                parsed_table = _process_table_block(current_table_lines, len(tables) + 1)
                if parsed_table:
                    tables.append(parsed_table)
                current_table_lines = []
                in_table = False

    if in_table and current_table_lines:
        parsed_table = _process_table_block(current_table_lines, len(tables) + 1)
        if parsed_table:
            tables.append(parsed_table)

    return tables


def _baris_lanjutan_judul(cells: list[str]) -> bool:
    """
    Baris sesudah separator yang sebenarnya masih judul kolom. Syaratnya ketat supaya baris
    kategori ("A | CPE") dan baris data tidak ikut tertelan: tanpa angka sama sekali, dan
    paling sedikit dua sel berisi kata judul yang dikenal.
    """
    isi = [c.strip() for c in cells if c.strip()]
    if not isi or any(re.search(r"\d", c) for c in isi):
        return False
    kata_judul = (
        DESC_KEYWORDS
        + QTY_KEYWORDS
        + UNIT_KEYWORDS
        + PERIOD_KEYWORDS
        + UNIT_PRICE_KEYWORDS
        + TOTAL_KEYWORDS
        + ["no", "harga"]
    )
    return sum(_header_matches(c, kata_judul) for c in isi) >= 2


def _gabung_judul_bertingkat(
    headers: list[str], data_lines: list[str]
) -> tuple[list[str], list[str]]:
    """
    Kontrak layanan Telkom menulis judul kolom dalam tiga baris ("Harga Kesepakatan" /
    "Harga Bulanan" / "OTC"). Markdown hanya mengenal satu baris judul, jadi dua baris
    lainnya dulu terbaca sebagai data: kolom uraian bernama "Kolom_2", detect_item_columns
    tidak menemukan apa pun, dan seluruh item KL jatuh ke jalur LLM. Baris lanjutan
    digabung ke judul kolomnya.
    """
    lanjutan: list[list[str]] = []
    sisa = list(data_lines)
    while sisa and len(lanjutan) < 3:
        if is_markdown_table_separator(sisa[0]):
            sisa.pop(0)
            continue
        cells = parse_markdown_table_row(sisa[0])
        if not _baris_lanjutan_judul(cells):
            break
        lanjutan.append(cells)
        sisa.pop(0)
    if not lanjutan:
        return headers, data_lines

    gabungan: list[str] = []
    for i, h in enumerate(headers):
        bagian = [] if re.fullmatch(r"Kolom_\d+(?:_\d+)?", h) else [re.sub(r"_\d+$", "", h)]
        bagian += [baris[i].strip() for baris in lanjutan if i < len(baris) and baris[i].strip()]
        nama = " ".join(bagian) or f"Kolom_{i + 1}"
        dasar, n = nama, 1
        while nama in gabungan:
            nama = f"{dasar}_{n}"
            n += 1
        gabungan.append(nama)
    return gabungan, sisa


def _process_table_block(lines: list[str], table_idx: int) -> dict[str, Any] | None:
    """Memproses sekumpulan baris markdown table menjadi dict terstruktur."""
    if len(lines) < 2:
        return None

    # Cari header dan separator
    header_idx = -1
    for i in range(len(lines)):
        if i + 1 < len(lines) and is_markdown_table_separator(lines[i + 1]):
            header_idx = i
            break

    if header_idx == -1:
        # Tidak ada separator standar, gunakan baris pertama sebagai header
        headers = [f"Kolom_{i + 1}" for i in range(len(parse_markdown_table_row(lines[0])))]
        data_lines = lines
    else:
        raw_headers = parse_markdown_table_row(lines[header_idx])
        # Buat header yang unik dan tidak kosong
        headers = []
        for col_idx, h in enumerate(raw_headers):
            col_name = h.strip() if h.strip() else f"Kolom_{col_idx + 1}"
            # Ensure unique column names
            base_col = col_name
            counter = 1
            while col_name in headers:
                col_name = f"{base_col}_{counter}"
                counter += 1
            headers.append(col_name)
        data_lines = lines[header_idx + 2 :]  # Lewati header & separator
        headers, data_lines = _gabung_judul_bertingkat(headers, data_lines)

    rows: list[dict[str, Any]] = []
    current_kategori: str | None = None

    for row_line in data_lines:
        if is_markdown_table_separator(row_line):
            continue

        cells = parse_markdown_table_row(row_line)
        if not any(cells):  # Skip completely empty rows
            continue

        # Pad or trim cells to match header length
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        else:
            cells = cells[: len(headers)]

        row_dict: dict[str, Any] = {}
        for h, c in zip(headers, cells):
            row_dict[h] = c

        # Deteksi apakah baris ini adalah Kategori / Header Kelompok (misal: "A. Tenaga Ahli")
        first_cell = cells[0].strip() if cells else ""
        second_cell = cells[1].strip() if len(cells) > 1 else ""

        non_empty = [c for c in cells if c]
        is_subtotal = any(
            kw in "".join(cells).lower()
            for kw in ["subtotal", "total a", "total b", "total keseluruhan"]
        )

        if not is_subtotal:
            if re.match(r"^[A-Z]$", first_cell) and second_cell:
                # Kategori baru, misal "A", "Tenaga Ahli"
                current_kategori = f"{first_cell}. {second_cell}"
                row_dict["_kategori_terdeteksi"] = current_kategori
            elif len(set(non_empty)) <= 2 and len(non_empty) >= 3:
                # Repeated text across cells (Docling table category representation)
                current_kategori = non_empty[0]
                row_dict["_kategori_terdeteksi"] = current_kategori
            elif current_kategori:
                row_dict["_kategori_terdeteksi"] = current_kategori

        rows.append(row_dict)

    return {
        "table_index": table_idx,
        "headers": headers,
        "row_count": len(rows),
        "rows": rows,
        "raw_markdown": "\n".join(lines),
    }


# Batas dua kata yang tersambung karena OCR membuang spasinya: "HargaSatuan", "JmlHarga".
# Minimal dua huruf kecil di kiri dan kata berawalan kapital di kanan, supaya singkatan
# berhuruf campur seperti "UoM" tidak ikut terbelah.
_KATA_MENYAMBUNG = re.compile(r"(?<=[a-z]{2})(?=[A-Z][a-z]{2})")


def _header_matches(header: str, include: list[str], exclude: list[str] = ()) -> bool:
    h = re.sub(r"[^a-z0-9 ]+", " ", _KATA_MENYAMBUNG.sub(" ", header).lower())
    h = " " + re.sub(r"\s+", " ", h).strip() + " "
    if any(f" {ex} " in h or (" " in ex and ex in h) for ex in exclude):
        return False
    return any(f" {inc} " in h or (" " in inc and inc in h) for inc in include)


def _find_col(headers: list[str], include: list[str], exclude: list[str] = ()) -> str | None:
    return next((h for h in headers if _header_matches(h, include, exclude)), None)


# Kata kunci header dicocokkan per kata (bukan substring): dulu "Harga Satuan" terpilih sebagai
# kolom unit (mengandung "satuan") dan "Jumlah Harga" sebagai kolom volume (mengandung "jumlah"),
# sehingga SPK menghasilkan volume=150000000 dan unit="150.000.000".
_PRICE_WORDS = ["harga", "price", "rp", "biaya", "total", "nilai", "amount", "mrc", "otc"]
DESC_KEYWORDS = [
    "uraian",
    "deskripsi",
    "description",
    "nama barang",
    "barang",
    "pekerjaan",
    "layanan",
    "item",
    "nama item",
    "jasa",
    "produk",
]
QTY_KEYWORDS = ["vol", "volume", "qty", "quantity", "jumlah", "jml", "kuantitas", "kuantiti"]
UNIT_KEYWORDS = ["sat", "satuan", "unit", "uom"]
PERIOD_KEYWORDS = ["periode", "perlode", "durasi", "bln", "bulan", "jangka waktu", "masa"]
UNIT_PRICE_KEYWORDS = [
    "harga satuan",
    "harga sat",  # singkatan di SPH/SPK berformat LKPP: "Harga Sat (Rp)" | "Jml Harga (Rp)"
    "satuan harga",
    "unit price",
    "rate",
    "harga bulanan",
    "mrc",
    "otc",
]
TOTAL_KEYWORDS = [
    "total harga",
    "jumlah harga",
    "harga total",
    "total",
    "amount",
    "jumlah biaya",
    "nilai",
]


_ANGKA_DALAM_SEL = re.compile(r"\d+(?:[.,]\d+)*")


def baca_volume(sel: Any) -> float | None:
    """
    Volume dari satu sel, atau None bila sel itu tidak memuat tepat satu angka yang bersih.

    Kolom "Vol" kadang dibelah dua sub-sel (SPH jasa: "1" orang dan "2" bulan). Markdown
    menyatukannya jadi "1 2", dan parse_indonesian_number membuang spasinya sehingga
    terbaca 12. Mengalikannya (1 x 2) juga bukan pilihan: itu angka hasil hitungan yang
    tidak tertulis. Sisa OCR seperti "12. id:)" sama berbahayanya -- 12 tampak sah padahal
    hurufnya bukti selnya tidak terbaca utuh. Keduanya dikosongkan; teks aslinya disimpan
    pemanggil di Atribut Tambahan supaya PM tetap melihatnya.
    """
    teks = str(sel or "").strip()
    if not teks or re.search(r"[^\w\s.,/()\-]", teks):
        return None
    angka = _ANGKA_DALAM_SEL.findall(teks)
    if len(angka) != 1:
        return None
    return parse_id_number(angka[0])


def detect_item_columns(headers: list[str]) -> dict[str, Any]:
    # "no" tidak masuk exclude: RapidOCR kadang menggabungkan kolom "No." ke header tetangga
    # ("Uraian Pekerjaan/ Layanan. No"), dan exclude "no" membuat header itu ditolak sehingga
    # Deskripsi terisi kolom volume.
    desc_col = _find_col(headers, DESC_KEYWORDS, _PRICE_WORDS)
    if not desc_col:
        desc_col = headers[1] if len(headers) > 1 else headers[0]
    qty_col = _find_col(headers, QTY_KEYWORDS, _PRICE_WORDS + ["group", "titik", "pembayaran"])
    price = [
        h
        for h in headers
        if _header_matches(h, UNIT_PRICE_KEYWORDS, ["total", "jumlah harga", "harga total"])
    ]
    total = [
        h
        for h in headers
        if _header_matches(
            h,
            TOTAL_KEYWORDS,
            ["harga satuan", "satuan harga", "unit price", "sub total", "subtotal"],
        )
    ]
    if not total:
        # "Jumlah" saja biasanya volume, tapi bila volume sudah punya kolom sendiri, kolom
        # "Jumlah" paling kanan adalah nominal baris: "Jumlah (Rp)" (SPH jasa), "Jumlah"
        # di sebelah "Qty" (SPH barang), "Jmlah Harga (Rp)" salah OCR (kontrak). Dulu kolom
        # ini masuk Atribut Tambahan dan totalnya dihitung vol x harga satuan.
        jumlah = [
            h
            for h in headers
            if h not in (qty_col, desc_col) and h not in price
            if _header_matches(h, ["jumlah", "jml", "jmlah"])
        ]
        total = jumlah[-1:]
    return {
        "desc": desc_col,
        "qty": qty_col,
        "unit": _find_col(headers, UNIT_KEYWORDS, _PRICE_WORDS),
        "period": _find_col(headers, PERIOD_KEYWORDS, _PRICE_WORDS),
        "price": price,
        "total": total,
    }


def _jenis_biaya_kolom(header: str) -> str | None:
    """
    "OTC" / "MRC" bila judul kolom menyebut tepat salah satunya, selain itu None. Judul yang
    menyebut keduanya (OCR menggabungkan dua judul: "Harga Satuan. MRC OTC") tidak dipakai
    menebak. "Bulanan" saja tidak berarti MRC: template Telkom menaruh OTC di bawah
    kelompok "Harga Bulanan".
    """
    otc = _header_matches(header, ["otc", "one time charge"])
    mrc = _header_matches(header, ["mrc", "monthly recurring charge", "monthly recurring"])
    if otc == mrc:
        return None
    return "OTC" if otc else "MRC"


def jenis_biaya_baris(row: dict[str, Any], kolom_harga: list[str]) -> str | None:
    """Jenis biaya satu baris dari judul kolom yang angkanya terisi."""
    jenis = {
        _jenis_biaya_kolom(k)
        for k in kolom_harga
        if (parse_indonesian_number(row.get(k, 0)) or 0) > 0
    } - {None}
    if jenis == {"OTC", "MRC"}:
        return "OTC dan MRC"
    return jenis.pop() if jenis else None


def clean_desc_and_extract_item_no(desc: str, current_no: str = "") -> tuple[str, str]:
    """
    Memisahkan nomor urut item yang mungkin terbawa di awal teks deskripsi/pekerjaan
    dan membersihkan trailing kata ringkasan seperti Subtotal/Grandtotal.

    Contoh:
    - "1 Penyediaan Fortigate 200f" -> ("1", "Penyediaan Fortigate 200f")
    - "3SSL" -> ("3", "SSL")
    - "5 Acess Point Subtotal (Sebelum PPN)" -> ("5", "Acess Point")
    """
    cleaned = desc.strip()
    # 1. Bersihkan trailing kata summary yang tertempel di baris terakhir
    cleaned = re.sub(
        r"(?i)\s+(?:subtotal|grandtotal|total)\s*(?:\([^)]*\))?.*$", "", cleaned
    ).strip()

    # 2. Deteksi nomor urut di awal teks
    m = re.match(r"^(\d+)(?:[\.\)\-\s]+|\b|\s*)([A-Za-z].*)$", cleaned)
    if m:
        extracted_no = m.group(1)
        clean_text = m.group(2).strip()
        return extracted_no, clean_text

    return current_no, cleaned


def extract_items_from_markdown_tables(
    markdown_text: str, doc_type: str = "sph"
) -> list[dict[str, Any]]:
    """
    Mengekstrak seluruh rincian baris barang/pekerjaan dari tabel dokumen secara deterministik.
    Menjamin tidak ada baris yang terpotong, terlewat, atau disingkat.
    Menjamin urutan nomor baris item tabel (1, 2, 3, dst.) selalu sinkron dan konsisten.
    """
    return [
        item
        for kelompok in extract_item_groups_from_markdown_tables(markdown_text, doc_type)
        for item in kelompok
    ]


def extract_item_groups_from_markdown_tables(
    markdown_text: str, doc_type: str = "sph"
) -> list[list[dict[str, Any]]]:
    """
    Sama dengan extract_items_from_markdown_tables, tetapi item dikelompokkan per tabel asal
    (tabel tanpa item tidak ikut). Pengelompokan dibutuhkan reconcile_items untuk mengenali
    bundel dokumen yang memuat tabel harga yang sama lebih dari sekali.
    """
    tables = extract_tables_from_markdown(markdown_text)
    kelompok_item: list[list[dict[str, Any]]] = []

    # BAST (Berita Acara Serah Terima) mendaftar barang/pekerjaan yang diserahkan TANPA kolom
    # harga -- tabel itu valid untuk BAST, tapi untuk kontrak/SPH tabel tanpa harga biasanya
    # daftar pejabat/jadwal/SLA, bukan BoQ.
    require_price = doc_type.lower() != "bast"

    current_kategori = None
    kategori_item_counter = 0
    table_item_counter = 0

    for table in tables:
        headers = table["headers"]
        extracted_items: list[dict[str, Any]] = []
        kelompok_item.append(extracted_items)

        cols = detect_item_columns(headers)
        desc_col, qty_col, unit_col, period_col = (
            cols["desc"],
            cols["qty"],
            cols["unit"],
            cols["period"],
        )
        price_cols, total_cols = cols["price"], cols["total"]

        if require_price and not price_cols and not total_cols:
            continue

        for r in table["rows"]:
            raw_desc = r.get(desc_col, "").strip()
            if not raw_desc:
                continue

            # Filter baris header kolom duplicate (misal: "No")
            if raw_desc.lower() in ["no", "no.", "nomor", "item"]:
                continue

            # Deteksi apakah baris ini adalah Kategori / Header Kelompok (misal: "A CPE License",
            # "A. Tenaga Ahli")
            other_cells = [
                v for k, v in r.items() if k != desc_col and not k.startswith("_") and v.strip()
            ]
            cat_m = re.match(r"^([A-Z])[\s\.\-]+([A-Za-z].*)$", raw_desc)
            if cat_m and not other_cells:
                current_kategori = f"{cat_m.group(1)}. {cat_m.group(2).strip()}"
                kategori_item_counter = 0
                continue
            elif re.match(r"^[A-Z]$", raw_desc) and not other_cells:
                current_kategori = raw_desc
                kategori_item_counter = 0
                continue

            # Filter baris header kategori (sel berisi teks berulang di seluruh kolom)
            cells_values = [v.strip() for k, v in r.items() if not k.startswith("_") and v.strip()]
            if len(set(cells_values)) <= 2 and len(cells_values) >= 3:
                continue

            no_val = r.get("No.", r.get("No", r.get("Nomor", r.get("Nomor Item", ""))))
            no_val, clean_desc = clean_desc_and_extract_item_no(
                raw_desc, str(no_val).strip() if no_val else ""
            )

            if not clean_desc:
                continue

            # Filter baris ringkasan / subtotal / grandtotal
            if bool(
                re.match(
                    r"^(?:subtotal|grandtotal|total\s*[a-z0-9]*|jumlah\s*total|total\s*keseluruhan)\b",
                    clean_desc,
                    re.IGNORECASE,
                )
            ):
                continue

            # Jika 'No' berisi huruf kategori tunggal (A, B) tanpa harga, skip
            if str(no_val) in ["A", "B", "C", "D", "E"] and not other_cells:
                current_kategori = clean_desc
                continue

            kategori = current_kategori or r.get("_kategori_terdeteksi")

            vol_str = str(r.get(qty_col) or "").strip() if qty_col else ""
            vol = baca_volume(vol_str) if vol_str else 1.0

            unit = r.get(unit_col, "Paket").strip() if unit_col and r.get(unit_col) else "Paket"
            periode = r.get(period_col, "").strip() if period_col and r.get(period_col) else None

            # Cari harga satuan efektif (prioritaskan kolom non-nol)
            h_sat = 0.0
            for pc in price_cols:
                val = parse_indonesian_number(r.get(pc, 0))
                if val > 0:
                    h_sat = val

            # Cari harga total efektif
            h_tot = 0.0
            for tc in total_cols:
                val = parse_indonesian_number(r.get(tc, 0))
                if val > 0:
                    h_tot = val

            # Harga yang tidak tertulis dibiarkan kosong, TIDAK dihitung dari volume x harga
            # satuan (aturan 3 AGENTS.md). Dulu total yang tidak terbaca diisi vol x harga:
            # satu SPH jasa menulis Jumlah per baris, tapi kolomnya tidak dikenali dan volume
            # "1 2" terbaca 12, jadi Total Harga keluar 6x lipat dari yang tertulis.

            # Identifikasi extra attributes
            exclude_keys = set(price_cols + total_cols) | {
                desc_col,
                qty_col,
                unit_col,
                period_col,
                "No.",
                "No",
                "Nomor",
                "Nomor Item",
            }
            extra = {
                k: v.strip()
                for k, v in r.items()
                if k not in exclude_keys and not k.startswith("_") and v and v.strip()
            }
            if vol is None:
                extra[qty_col] = vol_str

            # Baris tanpa harga di tabel BoQ = judul kelompok atau header yang terulang (hasil OCR),
            # bukan item yang ditagih. Untuk BAST, tidak adanya harga itu normal -- jangan di-skip.
            if require_price and h_sat == 0.0 and h_tot == 0.0:
                if not _header_matches(clean_desc, DESC_KEYWORDS + ["uralan"]):
                    current_kategori = clean_desc
                continue

            # Update sequence counter
            kategori_item_counter += 1
            table_item_counter += 1

            # Bersihkan nomor urut item
            if no_val and str(no_val).isdigit():
                item_seq_no = str(no_val)
            else:
                item_seq_no = str(kategori_item_counter if kategori else table_item_counter)

            if doc_type.lower() in ["contract", "spk"]:
                item_dict = {
                    "Nomor Item": item_seq_no,
                    "Kategori/Kelompok": kategori,
                    "Deskripsi Item/Barang/Pekerjaan": clean_desc,
                    "Spesifikasi": None,
                    "volume": vol,
                    "unit": unit,
                    "Periode/Durasi": periode,
                    "Harga Satuan": h_sat or None,
                    "Jumlah Harga": h_tot or None,
                    "Jenis Biaya": jenis_biaya_baris(r, price_cols + total_cols),
                    "Keterangan": None,
                    "Atribut Tambahan": extra if extra else None,
                }
            elif doc_type.lower() == "bast":
                item_dict = {
                    "No": item_seq_no,
                    "Deskripsi": clean_desc,
                    "Volume": vol,
                    "Satuan": unit,
                    "Hasil Uji Terima": None,
                    "Keterangan": None,
                    "Atribut Tambahan": extra if extra else None,
                }
            else:
                item_dict = {
                    "No": item_seq_no,
                    "Kategori/Kelompok": kategori,
                    "Nama Barang/Jasa": clean_desc,
                    "Spesifikasi": None,
                    "Brand/Merek": None,
                    "Part Number": None,
                    "Volume / Qty": vol,
                    "Satuan": unit,
                    "Periode/Durasi": periode,
                    "Harga Satuan": h_sat or None,
                    "Total Harga": h_tot or None,
                    "Jenis Biaya": jenis_biaya_baris(r, price_cols + total_cols),
                    "Keterangan": None,
                    "Atribut Tambahan": extra if extra else None,
                }
            extracted_items.append(item_dict)

    return [kelompok for kelompok in kelompok_item if kelompok]
