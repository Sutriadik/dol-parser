"""
Unit tests untuk Table Converter module (HTML table → Markdown table).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.parsers.table_converter import (
    html_table_to_markdown,
    html_table_to_rows,
    rows_to_markdown_table,
)


class TestHTMLTableToRows:
    def test_simple_table(self):
        html = """<table>
        <tr><td>A</td><td>B</td></tr>
        <tr><td>1</td><td>2</td></tr>
        </table>"""
        rows = html_table_to_rows(html)
        assert len(rows) == 2
        assert rows[0] == ["A", "B"]
        assert rows[1] == ["1", "2"]

    def test_table_with_th(self):
        html = """<table>
        <tr><th>Header 1</th><th>Header 2</th></tr>
        <tr><td>Data 1</td><td>Data 2</td></tr>
        </table>"""
        rows = html_table_to_rows(html)
        assert len(rows) == 2
        assert rows[0] == ["Header 1", "Header 2"]

    def test_colspan(self):
        html = """<table>
        <tr><td>A</td><td>B</td><td>C</td></tr>
        <tr><td colspan="2">Merged</td><td>D</td></tr>
        </table>"""
        rows = html_table_to_rows(html)
        assert len(rows) == 2
        assert rows[1] == ["Merged", "", "D"]

    def test_table_with_br(self):
        html = """<table>
        <tr><td>Line1<br>Line2</td><td>B</td></tr>
        </table>"""
        rows = html_table_to_rows(html)
        assert "Line1" in rows[0][0]
        assert "Line2" in rows[0][0]

    def test_spk_table(self):
        """Real-world test: tabel SPK dari PP-Structure output."""
        html = """<table>
        <tr><td>No.</td><td>Uraian Barang/Pekerjaan</td><td>Vol</td><td>Sat</td><td>Harga Satuan (Rp)</td><td>Jumlah Harga (Rp)</td></tr>
        <tr><td>1</td><td>Oracle Database Standart Edition 2</td><td>1</td><td>pkt</td><td>150.000.000</td><td>150.000.000</td></tr>
        <tr><td></td><td></td><td></td><td></td><td>Sub Total :</td><td>150.000.000</td></tr>
        <tr><td></td><td></td><td></td><td></td><td>PPN 11% :</td><td>16.500.000</td></tr>
        <tr><td></td><td></td><td></td><td></td><td>Total :</td><td>166.500.000</td></tr>
        </table>"""  # noqa: E501 (teks contoh dokumen)
        rows = html_table_to_rows(html)
        assert len(rows) == 5
        assert rows[0][0] == "No."
        assert rows[1][0] == "1"
        assert rows[1][4] == "150.000.000"
        assert "Sub Total" in rows[2][4]
        assert "166.500.000" in rows[4][5]

    def test_empty_table(self):
        rows = html_table_to_rows("<table></table>")
        assert rows == []

    def test_no_table_tag(self):
        rows = html_table_to_rows("just some text")
        assert rows == []


class TestRowsToMarkdownTable:
    def test_basic(self):
        rows = [["Name", "Age"], ["Alice", "30"], ["Bob", "25"]]
        md = rows_to_markdown_table(rows)
        lines = md.strip().split("\n")
        assert len(lines) == 4  # header + separator + 2 data rows
        assert "Name" in lines[0]
        assert "---" in lines[1]
        assert "Alice" in lines[2]

    def test_uneven_rows(self):
        rows = [["A", "B", "C"], ["1", "2"]]
        md = rows_to_markdown_table(rows)
        lines = md.strip().split("\n")
        # Row 2 should be padded to 3 columns
        assert lines[2].count("|") == 4  # 3 cells = 4 pipes

    def test_single_row_no_header(self):
        rows = [["A", "B"]]
        md = rows_to_markdown_table(rows, has_header=False)
        lines = md.strip().split("\n")
        assert len(lines) == 1  # no separator

    def test_empty_rows(self):
        assert rows_to_markdown_table([]) == ""


class TestHTMLTableToMarkdown:
    def test_full_conversion(self):
        html = """<table>
        <tr><th>Col1</th><th>Col2</th></tr>
        <tr><td>Data1</td><td>Data2</td></tr>
        </table>"""
        md = html_table_to_markdown(html)
        assert "Col1" in md
        assert "Data1" in md
        assert "---" in md

    def test_returns_empty_for_no_table(self):
        assert html_table_to_markdown("no table here") == ""
        assert html_table_to_markdown("") == ""

    def test_returns_empty_for_single_row(self):
        html = "<table><tr><td>Only one row</td></tr></table>"
        assert html_table_to_markdown(html) == ""

    def test_financial_table(self):
        html = """<table>
        <tr><td>Revenue</td><td>$ 57,006</td><td>$ 35,082</td></tr>
        <tr><td>Cost</td><td>15,157</td><td>8,926</td></tr>
        <tr><td>Gross profit</td><td>41,849</td><td>26,156</td></tr>
        </table>"""
        md = html_table_to_markdown(html)
        assert "Revenue" in md
        assert "57,006" in md
        assert "Gross profit" in md


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
