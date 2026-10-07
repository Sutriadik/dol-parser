"""
Open ADE — Rule & cross-field validation (Plan §15).

Pydantic hanya menjamin tipe/struktur. Modul ini memeriksa apakah nilainya masuk akal
secara bisnis: total = subtotal + PPN, terbilang = total, jumlah item = subtotal, dst.
Semua rule deterministik, tanpa LLM.
"""

import re
from collections.abc import Callable
from typing import Any

from app.extractors.deterministic.dates import find_dates, parse_id_date
from app.extractors.deterministic.numbers import amounts_equal, parse_id_number, terbilang_to_number
from app.schemas.evidence import Severity, ValidationIssue, ValidationReport

RULE_VERSION = "rules-2026.09.3"
# Toleransi absolut untuk selisih pembulatan PPN/harga satuan. Toleransi relatif (0.5%) sempat
# dicoba tapi meloloskan terbilang yang salah ~Rp824 ribu pada nilai kontrak Rp174 juta.
ROUNDING_TOLERANCE = 1000.0


def _num(value: Any) -> float | None:
    parsed = parse_id_number(value)
    return parsed if parsed not in (None, 0.0) else None


def _close(a: float | None, b: float | None, tolerance: float = ROUNDING_TOLERANCE) -> bool:
    if a is None or b is None:
        return False
    return amounts_equal(a, b, tolerance=tolerance)


def _percent(value: Any) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(value or ""))
    return float(match.group(1).replace(",", ".")) / 100 if match else None


class _Checker:
    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []
        self.checked: list[str] = []

    def rule(self, name: str) -> None:
        self.checked.append(name)

    def add(
        self,
        rule: str,
        severity: Severity,
        fields: list[str],
        message: str,
        expected: Any = None,
        actual: Any = None,
    ) -> None:
        self.issues.append(
            ValidationIssue(
                rule=rule,
                severity=severity,
                fields=fields,
                message=message,
                expected=expected,
                actual=actual,
            )
        )

    def report(self) -> ValidationReport:
        if any(i.severity == Severity.ERROR for i in self.issues):
            status = "fail"
        elif self.issues:
            status = "warn"
        else:
            status = "pass"
        return ValidationReport(
            status=status, rule_version=RULE_VERSION, checked_rules=self.checked, issues=self.issues
        )


def _check_totals(
    c: _Checker,
    subtotal_key: str,
    ppn_key: str,
    total_key: str,
    rate_key: str,
    data: dict[str, Any],
) -> None:
    subtotal, ppn, total = (
        _num(data.get(subtotal_key)),
        _num(data.get(ppn_key)),
        _num(data.get(total_key)),
    )

    c.rule("total_equals_subtotal_plus_ppn")
    if subtotal and total:
        expected_total = subtotal + (ppn or 0.0)
        if not _close(expected_total, total):
            c.add(
                "total_equals_subtotal_plus_ppn",
                Severity.ERROR,
                [subtotal_key, ppn_key, total_key],
                f"{subtotal_key} + {ppn_key} tidak sama dengan {total_key}",
                expected=expected_total,
                actual=total,
            )
        if total < subtotal:
            c.add(
                "total_not_below_subtotal",
                Severity.ERROR,
                [subtotal_key, total_key],
                f"{total_key} lebih kecil dari {subtotal_key}",
                expected=f">= {subtotal}",
                actual=total,
            )

    c.rule("ppn_matches_rate")
    rate = _percent(data.get(rate_key))
    if subtotal and ppn and rate:
        # PPN 12% dengan DPP nilai lain (11/12) menghasilkan tarif efektif 11%.
        candidates = [subtotal * rate, subtotal * rate * 11 / 12]
        if not any(_close(ppn, cand) for cand in candidates):
            c.add(
                "ppn_matches_rate",
                Severity.WARNING,
                [ppn_key, rate_key],
                f"{ppn_key} tidak sesuai tarif {data.get(rate_key)} dari {subtotal_key}",
                expected=round(candidates[0], 2),
                actual=ppn,
            )


def _check_items(
    c: _Checker,
    items_key: str,
    qty_key: str,
    price_key: str,
    line_total_key: str,
    subtotal_key: str,
    total_key: str,
    data: dict[str, Any],
) -> None:
    items = data.get(items_key) or []
    c.rule("item_line_total")
    line_sum = 0.0
    all_lines_have_total = bool(items)
    for idx, item in enumerate(items):
        qty, price, line_total = (
            _num(item.get(qty_key)),
            _num(item.get(price_key)),
            _num(item.get(line_total_key)),
        )
        if line_total is None:
            all_lines_have_total = False
            continue
        line_sum += line_total
        if qty and price:
            periode = (
                _num(re.sub(r"[^\d.,]", "", str(item.get("Periode/Durasi") or "")))
                if item.get("Periode/Durasi")
                else None
            )
            candidates = [qty * price] + ([qty * price * periode] if periode else [])
            extra = item.get("Atribut Tambahan") or {}
            for extra_value in extra.values():
                multiplier = _num(extra_value)
                if multiplier and multiplier < 10_000:
                    candidates.append(qty * price * multiplier)
            if not any(_close(line_total, cand) for cand in candidates):
                base = f"{items_key}[{idx}]"
                c.add(
                    "item_line_total",
                    Severity.WARNING,
                    [f"{base}.{qty_key}", f"{base}.{price_key}", f"{base}.{line_total_key}"],
                    f"Item {idx + 1}: volume x harga satuan tidak sama dengan jumlah harga",
                    expected=round(qty * price, 2),
                    actual=line_total,
                )

    c.rule("items_sum_matches_subtotal")
    subtotal, total = _num(data.get(subtotal_key)), _num(data.get(total_key))
    if (
        all_lines_have_total
        and line_sum
        and (subtotal or total)
        and not (_close(line_sum, subtotal) or _close(line_sum, total))
    ):
        c.add(
            "items_sum_matches_subtotal",
            Severity.WARNING,
            [items_key, subtotal_key],
            "Jumlah harga seluruh item tidak sama dengan subtotal maupun total",
            expected=subtotal,
            actual=line_sum,
        )


def _check_terbilang(
    c: _Checker, terbilang_key: str, amount_keys: list[str], data: dict[str, Any]
) -> None:
    c.rule("terbilang_matches_amount")
    words = data.get(terbilang_key)
    if not words:
        return
    spelled = terbilang_to_number(words)
    amounts = [(k, _num(data.get(k))) for k in amount_keys]
    amounts = [(k, v) for k, v in amounts if v]
    if spelled is None or not amounts:
        return
    if not any(_close(float(spelled), v, tolerance=1.0) for _, v in amounts):
        c.add(
            "terbilang_matches_amount",
            Severity.ERROR,
            [terbilang_key],
            "Jumlah terbilang tidak sama dengan nominal",
            expected=amounts[0][1],
            actual=spelled,
        )


def _check_required(c: _Checker, keys: list[str], data: dict[str, Any]) -> None:
    c.rule("required_fields")
    for key in keys:
        node: Any = data
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None or (isinstance(node, str) and not node.strip()):
            c.add("required_fields", Severity.WARNING, [key], f"Field wajib '{key}' kosong")


def _check_date_range(c: _Checker, key: str, data: dict[str, Any]) -> None:
    c.rule("date_range_order")
    dates = find_dates(str(data.get(key) or ""))
    if len(dates) >= 2 and dates[1] < dates[0]:
        c.add(
            "date_range_order",
            Severity.ERROR,
            [key],
            "Tanggal selesai lebih awal dari tanggal mulai",
            expected=f">= {dates[0]}",
            actual=str(dates[1]),
        )


def _check_parties_distinct(
    c: _Checker, data: dict[str, Any], p1_key: str = "Pihak Pertama", p2_key: str = "Pihak Kedua"
) -> None:
    c.rule("parties_distinct")
    p1, p2 = data.get(p1_key) or {}, data.get(p2_key) or {}
    for key in ("Nama Perusahaan", "Alamat"):
        v1, v2 = (p1.get(key) or "").strip().lower(), (p2.get(key) or "").strip().lower()
        if v1 and v1 == v2:
            c.add(
                "parties_distinct",
                Severity.ERROR,
                [f"{p1_key}.{key}", f"{p2_key}.{key}"],
                f"{key} {p1_key} dan {p2_key} identik (kemungkinan tertukar/tersalin)",
            )


def _check_field_date_order(
    c: _Checker, rule: str, earlier_key: str, later_key: str, data: dict[str, Any]
) -> None:
    c.rule(rule)
    earlier, later = (
        parse_id_date(str(data.get(earlier_key) or "")),
        parse_id_date(str(data.get(later_key) or "")),
    )
    if earlier and later and later < earlier:
        c.add(
            rule,
            Severity.ERROR,
            [earlier_key, later_key],
            f"{later_key} ({later}) lebih awal dari {earlier_key} ({earlier})",
            expected=f">= {earlier}",
            actual=str(later),
        )


def validate_contract(data: dict[str, Any]) -> ValidationReport:
    c = _Checker()
    _check_required(
        c,
        [
            "Nomor Kontrak Kerja",
            "Nama Pekerjaan",
            "Pihak Pertama.Nama Perusahaan",
            "Pihak Kedua.Nama Perusahaan",
        ],
        data,
    )
    _check_totals(c, "sub total", "Total PPN", "Total Harga Pekerjaan", "persentase ppn", data)
    _check_items(
        c,
        "List Item/Barang",
        "volume",
        "Harga Satuan",
        "Jumlah Harga",
        "sub total",
        "Total Harga Pekerjaan",
        data,
    )
    _check_terbilang(c, "Jumlah Terbilang", ["Total Harga Pekerjaan", "sub total"], data)
    _check_date_range(c, "Jangka Waktu", data)
    _check_parties_distinct(c, data)
    return c.report()


def validate_sph(data: dict[str, Any]) -> ValidationReport:
    c = _Checker()
    _check_required(c, ["Nomor SPH", "Vendor.Nama Vendor"], data)
    _check_totals(c, "Subtotal", "Nilai PPN", "Grand Total", "Persentase PPN", data)
    _check_items(
        c,
        "Daftar Penawaran Harga",
        "Volume / Qty",
        "Harga Satuan",
        "Total Harga",
        "Subtotal",
        "Grand Total",
        data,
    )
    return c.report()


def validate_bast(data: dict[str, Any]) -> ValidationReport:
    c = _Checker()
    _check_required(c, ["Pihak Pertama.Nama Perusahaan", "Pihak Kedua.Nama Perusahaan"], data)
    _check_parties_distinct(c, data)
    _check_field_date_order(
        c, "serah_terima_after_po_kontrak", "Tanggal PO / Kontrak", "Tanggal Serah Terima", data
    )

    c.rule("references_source_document")
    if not data.get("Nomor PO / Kontrak") and not data.get("Nama Pekerjaan"):
        c.add(
            "references_source_document",
            Severity.WARNING,
            ["Nomor PO / Kontrak", "Nama Pekerjaan"],
            "BAST tidak merujuk ke nomor PO/Kontrak maupun nama pekerjaan apa pun -- sulit "
            "ditautkan ke dokumen sumbernya",
        )

    c.rule("has_items")
    if not data.get("Daftar Barang/Pekerjaan Diserahkan"):
        c.add(
            "has_items",
            Severity.WARNING,
            ["Daftar Barang/Pekerjaan Diserahkan"],
            "Tidak ada barang/pekerjaan yang tercatat diserahterimakan",
        )
    return c.report()


# Nominal uang yang menentukan tagihan. Nilainya harus bisa ditunjukkan di dokumen.
AMOUNT_FIELDS: dict[str, list[str]] = {
    "contract": ["sub total", "Total PPN", "Total Harga Pekerjaan"],
    "sph": ["Subtotal", "Nilai PPN", "Grand Total"],
    "bast": ["Nilai Pengadaan"],
}


def check_amounts_grounded(
    report: ValidationReport, doc_type: str, evidence: list[Any]
) -> ValidationReport:
    """
    Rule lanjutan yang baru bisa dijalankan SETELAH grounding: nominal uang yang tidak
    ditemukan di dokumen (status UNSUPPORTED) tidak boleh ikut lolos sebagai `pass`.

    Cek aritmetika saja buta terhadap angka yang dikarang secara konsisten: SPH PT Vendor A
    pernah lolos `pass` dengan PPN dan Grand Total hasil hitung ulang (subtotal x tarif),
    padahal keduanya tidak tertulis di dokumen dan grounding sudah menandainya UNSUPPORTED.
    """
    c = _Checker()
    c.issues, c.checked = list(report.issues), list(report.checked_rules)
    c.rule("amounts_grounded")
    fields = set(AMOUNT_FIELDS.get(doc_type, []))
    for ev in evidence:
        if ev.field in fields and getattr(ev.status, "value", ev.status) == "UNSUPPORTED":
            c.add(
                "amounts_grounded",
                Severity.WARNING,
                [ev.field],
                f"Nominal '{ev.field}' tidak ditemukan di dokumen -- periksa terhadap PDF asli",
                actual=ev.value,
            )
    return c.report()


def note_duplicate_pages(report: ValidationReport, duplicate_pages: list[int]) -> ValidationReport:
    """
    Beri tahu PM bila berkas berisi salinan ganda yang tidak ikut diekstrak.

    Membuang halaman diam-diam berbahaya: kalau salinan kedua ternyata berbeda (mis. revisi
    harga), PM harus tahu bahwa halaman itu tidak dibaca AI.
    """
    c = _Checker()
    c.issues, c.checked = list(report.issues), list(report.checked_rules)
    c.rule("duplicate_copy")
    if duplicate_pages:
        c.add(
            "duplicate_copy",
            Severity.WARNING,
            [],
            f"Berkas berisi salinan ganda: halaman {duplicate_pages[0]}-{duplicate_pages[-1]} "
            f"mengulang halaman sebelumnya dan tidak ikut diekstrak. Pastikan isinya memang "
            f"sama (bukan revisi).",
            actual=duplicate_pages,
        )
    return c.report()


_VALIDATORS: dict[str, Callable[[dict[str, Any]], ValidationReport]] = {
    "contract": validate_contract,
    "sph": validate_sph,
    "bast": validate_bast,
}


def validate_extraction(doc_type: str, data: dict[str, Any]) -> ValidationReport:
    validator = _VALIDATORS.get(doc_type, validate_contract)
    return validator(data)
