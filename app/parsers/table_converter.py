"""
Open ADE — LaTeX-Grade Table Converter Module

Mengonversi tabel HTML (dari Docling / PPStructure SLANet) atau OCR bounding boxes menjadi
Markdown table 2D yang bersih, terstruktur, dan beralignment presisi (setara tabular LaTeX).
"""

import re
from html.parser import HTMLParser
from typing import Any


class _TableHTMLParser(HTMLParser):
    """
    Parser HTML yang mengekstrak isi sel tabel
    menjadi List[List[str]] (rows × cols) dengan handling colspan & rowspan.
    """

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: str = ""
        self._in_cell = False
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = ""
            self._colspan = 1
            for attr_name, attr_value in attrs:
                if attr_name == "colspan" and attr_value:
                    try:
                        self._colspan = int(attr_value)
                    except ValueError:
                        self._colspan = 1
        elif tag == "br" and self._in_cell:
            self._current_cell += " "

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in ("td", "th"):
            self._in_cell = False
            cell_text = self._current_cell.strip()
            cell_text = re.sub(r"\s+", " ", cell_text)
            self._current_row.append(cell_text)
            for _ in range(self._colspan - 1):
                self._current_row.append("")
        elif tag == "tr":
            if self._current_row and any(c.strip() for c in self._current_row):
                self.rows.append(self._current_row)

    def handle_data(self, data: str):
        if self._in_cell:
            self._current_cell += data


def html_table_to_rows(html_str: str) -> list[list[str]]:
    """Parse HTML <table> string menjadi list of rows."""
    parser = _TableHTMLParser()
    parser.feed(html_str)
    return parser.rows


def _is_numeric_cell(cell: str) -> bool:
    """Deteksi apakah isi sel adalah angka, nominal mata uang, atau persentase."""
    c = cell.strip()
    if not c:
        return False
    # Bersihkan Rp, titik ribuan, koma desimal, persen, dash
    c_clean = re.sub(r"^(?:Rp\.?|IDR|\$)\s*", "", c, flags=re.IGNORECASE)
    c_clean = c_clean.rstrip("%-–—").strip()
    # Cocokkan angka integer / float / currency berformat (misal 150.000.000,00 atau 12 atau 11%)
    return bool(re.match(r"^-?\d+(?:[\.,]\d+)*$", c_clean))


def _determine_column_alignment(col_cells: list[str], header_text: str = "") -> str:
    """
    Menentukan alignment LaTeX-style untuk kolom:
    - 'right' (---:) untuk nominal harga, volume, total, persentase
    - 'center' (:---:) untuk No, Satuan, Kode singkat
    - 'left' (:---) untuk deskripsi pekerjaan, nama, alamat, teks narasi
    """
    non_empty = [c for c in col_cells if c.strip()]
    if not non_empty:
        return "left"

    header_lower = header_text.lower().strip()
    if any(
        kw in header_lower
        for kw in [
            "harga",
            "jumlah",
            "total",
            "subtotal",
            "sub total",
            "vol",
            "volume",
            "nilai",
            "tarif",
            "rp",
            "ppn",
            "biaya",
        ]
    ):
        return "right"
    if any(
        kw in header_lower
        for kw in ["no", "no.", "nomor", "sat", "satuan", "unit", "kode", "status"]
    ):
        return "center"

    numeric_count = sum(1 for c in non_empty if _is_numeric_cell(c))
    if numeric_count / len(non_empty) >= 0.7:
        return "right"

    avg_len = sum(len(c) for c in non_empty) / len(non_empty)
    if avg_len <= 4 and all(len(c) <= 6 for c in non_empty):
        return "center"

    return "left"


def _clean_cell_content(cell: str) -> str:
    """Membersihkan isi sel dan meng-escape karakter pipa markdown."""
    c = str(cell).strip()
    c = re.sub(r"\s+", " ", c)
    # Escape pipe char jika tidak di-escape
    c = re.sub(r"(?<!\\)\|", r"\|", c)

    # Perbaiki typo nomor menempel di awal kata seperti '1Penyediaan' -> '1. Penyediaan', '2Cpanel'
    # -> '2. Cpanel', '3SSL' -> '3. SSL', '4APJII' -> '4. APJII'
    c = re.sub(r"^(\d{1,2})([A-Za-z])", r"\1. \2", c)

    # Perbaiki kata satuan/waktu menempel ke angka: "Paket12" -> "Paket 12"
    c = re.sub(r"\b(Paket|Bulan|Tahun|Hari|Item|pkt|Pkt)(\d+)\b", r"\1 \2", c)

    # Perbaiki typo umum seperti 'Acess' -> 'Access', 'Standart' -> 'Standard'
    c = re.sub(r"\bAcess\b", "Access", c, flags=re.IGNORECASE)
    c = re.sub(r"\bStandart\b", "Standard", c, flags=re.IGNORECASE)
    return c


def rows_to_markdown_table(rows: list[list[str]], has_header: bool = True) -> str:
    """
    Konversi list of rows menjadi Markdown table 2D berformat LaTeX tabular.
    - Monospace column padding presisi
    - Alignment: Rata kanan untuk angka/nominal, rata kiri untuk deskripsi, rata tengah untuk
      No/Satuan.
    """
    if not rows:
        return ""

    # Filter baris yang benar-benar kosong
    valid_rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not valid_rows:
        return ""

    max_cols = max(len(row) for row in valid_rows)
    if max_cols < 2:
        return ""

    normalized_rows = []
    for row in valid_rows:
        padded = row + [""] * (max_cols - len(row))
        normalized_rows.append([_clean_cell_content(c) for c in padded])

    # Tentukan alignment per kolom
    header_row = normalized_rows[0] if has_header else [""] * max_cols
    col_alignments = []
    for col_idx in range(max_cols):
        col_cells = [r[col_idx] for r in (normalized_rows[1:] if has_header else normalized_rows)]
        align = _determine_column_alignment(
            col_cells, header_row[col_idx] if col_idx < len(header_row) else ""
        )
        col_alignments.append(align)

    # Hitung lebar kolom optimal (minimal 3, maksimal 80 untuk keterbacaan)
    col_widths = []
    for col_idx in range(max_cols):
        max_width = 3
        for row in normalized_rows:
            cell_len = len(row[col_idx]) if col_idx < len(row) else 0
            max_width = max(max_width, cell_len)
        col_widths.append(max(max_width, 4))

    lines = []
    for row_idx, row in enumerate(normalized_rows):
        cells = []
        for col_idx, cell in enumerate(row):
            width = col_widths[col_idx]
            align = col_alignments[col_idx]
            if align == "right":
                cells.append(f" {cell:>{width}} ")
            elif align == "center":
                cells.append(f" {cell:^{width}} ")
            else:
                cells.append(f" {cell:<{width}} ")
        line = "|" + "|".join(cells) + "|"
        lines.append(line)

        if row_idx == 0 and has_header:
            sep_cells = []
            for col_idx, width in enumerate(col_widths):
                align = col_alignments[col_idx]
                dash_len = width + 2
                if align == "right":
                    sep = "-" * (dash_len - 1) + ":"
                elif align == "center":
                    sep = ":" + "-" * (dash_len - 2) + ":"
                else:
                    sep = ":" + "-" * (dash_len - 1)
                sep_cells.append(sep)
            sep_line = "|" + "|".join(sep_cells) + "|"
            lines.append(sep_line)

    return "\n".join(lines)


def html_table_to_markdown(html_str: str) -> str:
    """Konversi HTML <table> string langsung ke Markdown table LaTeX-grade."""
    if not html_str or "<table" not in html_str.lower():
        return ""
    rows = html_table_to_rows(html_str)
    if len(rows) < 2:
        return ""
    return rows_to_markdown_table(rows, has_header=True)


def parse_markdown_table_to_rows(table_md: str) -> list[list[str]]:
    """Parse teks tabel Markdown yang ada menjadi list of rows."""
    lines = [line.strip() for line in table_md.strip().split("\n") if line.strip()]
    rows = []
    for line in lines:
        if not line.startswith("|") or not line.endswith("|"):
            continue
        # Abaikan separator line |---|---|
        if re.match(r"^\|[\s\-:]+(\|[\s\-:]+)+\|$", line):
            continue
        parts = [c.strip() for c in line[1:-1].split("|")]
        rows.append(parts)
    return rows


def reformat_markdown_tables_in_text(text: str) -> str:
    """
    Mendeteksi semua tabel Markdown dalam teks dokumen dan
    memformat ulang menjadi format LaTeX-grade yang rapi dan ter-align.
    """
    if not text or "|" not in text:
        return text

    table_pattern = re.compile(r"((?:^[ \t]*\|[^\n]+\|[ \t]*\n)+)", re.MULTILINE)

    def _replace_table(match):
        raw_table = match.group(1)
        rows = parse_markdown_table_to_rows(raw_table)
        if len(rows) >= 2:
            formatted = rows_to_markdown_table(rows, has_header=True)
            if formatted:
                return "\n" + formatted + "\n"
        return raw_table

    return table_pattern.sub(_replace_table, text)


def format_structured_tabular_boxes(boxes: list[Any], y_tol: float = 14) -> str:
    """
    Membangun representasi teks terstruktur dari bounding box OCR:
    - Memisahkan kop surat (pre-table text)
    - Menyusun tabel dalam 2D Markdown Grid
    - Memisahkan tanda tangan (post-table text)
    """
    if not boxes:
        return ""

    sorted_boxes = sorted(boxes, key=lambda b: (b.ymin, b.xmin))
    rows = []

    for b in sorted_boxes:
        placed = False
        for r in rows:
            r_avg_y = sum(item.center_y for item in r) / len(r)
            if abs(b.center_y - r_avg_y) <= y_tol:
                r.append(b)
                placed = True
                break
        if not placed:
            rows.append([b])

    for r in rows:
        r.sort(key=lambda b: b.xmin)
    rows.sort(key=lambda r: min(item.ymin for item in r))

    # Deteksi batas awal dan akhir tabel
    table_start_idx = -1
    table_end_idx = -1

    table_header_keywords = [
        "uraian",
        "pekerjaan",
        "barang",
        "volume",
        "satuan",
        "harga satuan",
        "harga total",
        "jumlah",
        "jangka waktu",
        "kuantitas",
        "spesifikasi",
    ]
    table_footer_keywords = [
        "subtotal",
        "grandtotal",
        "total harga",
        "total penawaran",
        "total a",
        "total b",
        "total akhir",
        "total keseluruhan",
    ]

    for idx, r in enumerate(rows):
        row_text = " ".join(b.text for b in r).lower()
        if table_start_idx == -1:
            if any(kw in row_text for kw in table_header_keywords) and len(r) >= 2:
                table_start_idx = idx
        else:
            if any(kw in row_text for kw in table_footer_keywords):
                table_end_idx = idx
            elif (
                table_end_idx != -1
                and idx > table_end_idx + 1
                and any(
                    kw in row_text
                    for kw in ["hormat kami", "pihak", "direktur", "ttd", "materai", "tanda tangan"]
                )
            ):
                break

    if table_start_idx != -1 and table_end_idx == -1:
        for idx in range(len(rows) - 1, table_start_idx, -1):
            r = rows[idx]
            if len(r) >= 3 or any(re.search(r"\d+[\.,]\d+", b.text) for b in r):
                table_end_idx = idx
                break

    if table_start_idx != -1 and table_end_idx != -1 and table_end_idx >= table_start_idx:
        pre_table_rows = rows[:table_start_idx]
        table_rows = rows[table_start_idx : table_end_idx + 1]
        post_table_rows = rows[table_end_idx + 1 :]

        parts = []
        if pre_table_rows:
            parts.append("\n".join(" ".join(b.text for b in r) for r in pre_table_rows))

        # Format tabel 2D
        col_anchors = []
        for r in table_rows:
            if len(r) >= 3:
                for b in r:
                    matched = False
                    for c_idx, (c_min, c_max) in enumerate(col_anchors):
                        if not (b.xmax < c_min - 35 or b.xmin > c_max + 35):
                            col_anchors[c_idx] = (min(c_min, b.xmin), max(c_max, b.xmax))
                            matched = True
                            break
                    if not matched:
                        col_anchors.append((b.xmin, b.xmax))
        col_anchors.sort(key=lambda c: c[0])

        if len(col_anchors) >= 2:
            grid = []
            for r in table_rows:
                cells = [""] * len(col_anchors)
                for b in r:
                    best_col = min(
                        range(len(col_anchors)),
                        key=lambda c: abs(
                            (b.xmin + b.xmax) / 2 - (col_anchors[c][0] + col_anchors[c][1]) / 2
                        ),
                    )
                    if cells[best_col]:
                        cells[best_col] += " " + b.text.strip()
                    else:
                        cells[best_col] = b.text.strip()
                grid.append(cells)

            md_table = rows_to_markdown_table(grid, has_header=True)
            if md_table:
                parts.append("\n" + md_table + "\n")
            else:
                parts.append("\n".join(" ".join(b.text for b in r) for r in table_rows))
        else:
            parts.append("\n".join(" ".join(b.text for b in r) for r in table_rows))

        if post_table_rows:
            parts.append("\n".join(" ".join(b.text for b in r) for r in post_table_rows))

        return "\n\n".join(parts)

    return "\n".join(" ".join(b.text for b in r) for r in rows)


def clean_table_cells(rows: list[list[str]]) -> list[list[str]]:
    """Membersihkan sel tabel dari spasi berlebih."""
    cleaned = []
    for row in rows:
        cleaned_row = []
        for cell in row:
            cell = cell.strip()
            cell = re.sub(r"\s+", " ", cell)
            cleaned_row.append(cell)
        cleaned.append(cleaned_row)
    return cleaned
