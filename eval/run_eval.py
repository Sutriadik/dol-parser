"""
Open ADE — Evaluation runner (Plan Phase 0/1: baseline + golden dataset).

Membandingkan output *.extract.json terhadap golden file field-by-field,
tanpa menjalankan parser/LLM ulang. Jadi setiap perubahan bisa dibuktikan
naik/turun akurasinya.

Pemakaian:
    python -m eval.run_eval                                   # evaluasi storage/outputs/extraction
    python -m eval.run_eval --pred-dir path/ --label after_fix  # simpan report dengan label
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extractors.deterministic.dates import parse_id_date  # noqa: E402
from app.extractors.deterministic.numbers import parse_id_number, terbilang_to_number  # noqa: E402

GOLDEN_DIR = ROOT / "eval" / "golden"
REPORT_DIR = ROOT / "eval" / "reports"
DEFAULT_PRED_DIR = ROOT / "storage" / "outputs" / "extraction"
VERIFIED_STATUSES = {"AUTO_VERIFIED", "AUTO_ACCEPTED"}


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, (dict, list)) and key != "Syarat Lampiran Wajib BAST":
                flat.update(flatten(value, path))
            else:
                flat[path] = value
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            flat.update(flatten(value, f"{prefix}[{idx}]"))
    else:
        flat[prefix] = data
    return flat


def canon(text: Any) -> str:
    s = str(text).lower()
    s = re.sub(r"\bji\.", "jl.", s)
    s = re.sub(r"[^\w/%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def token_f1(a: str, b: str) -> float:
    ta, tb = set(canon(a).split()), set(canon(b).split())
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    if not overlap:
        return 0.0
    p, r = overlap / len(tb), overlap / len(ta)
    return 2 * p * r / (p + r)


def is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == []


def compare(expected: Any, predicted: Any) -> tuple[str, str]:
    """Kembalikan (verdict, method).

    verdict: exact | lenient | wrong | missing | correct_null | spurious.
    """
    if is_empty(expected):
        return ("correct_null", "null") if is_empty(predicted) else ("spurious", "null")
    if is_empty(predicted):
        return "missing", "-"

    if isinstance(expected, (int, float)):
        num = parse_id_number(predicted)
        return (
            ("exact", "number")
            if num is not None and abs(num - expected) <= 1
            else ("wrong", "number")
        )

    exp_s, pred_s = str(expected), str(predicted)
    exp_date = (
        parse_id_date(exp_s)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2} \w+ \d{4}", exp_s.strip())
        else None
    )
    if exp_date:
        pred_date = parse_id_date(pred_s)
        return ("exact", "date") if pred_date == exp_date else ("wrong", "date")

    if re.search(r"\b(ribu|juta|miliar|milyar|rupiah)\b", exp_s, re.IGNORECASE):
        exp_num, pred_num = terbilang_to_number(exp_s), terbilang_to_number(pred_s)
        return (
            ("exact", "terbilang")
            if exp_num is not None and exp_num == pred_num
            else ("wrong", "terbilang")
        )

    if canon(exp_s) == canon(pred_s):
        return "exact", "canon"
    extra_tokens = len(canon(pred_s).split()) - len(canon(exp_s).split())
    if token_f1(exp_s, pred_s) >= 0.85 or (canon(exp_s) in canon(pred_s) and extra_tokens <= 4):
        return "lenient", "token_f1"
    return "wrong", "token_f1"


# Akurasi diukur pada field yang benar-benar disimpan & diperiksa PM. Daftarnya sama dengan
# pemeta (app/companion/common.py), supaya eval tidak menilai field yang tidak dipakai siapa pun.
from app.companion.common import disimpan as _diekstrak  # noqa: E402


def evaluate_document(golden: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    pred_flat = flatten(prediction.get("data", {}))
    evidence = (
        {e["field"]: e for e in prediction.get("evidence", [])}
        if isinstance(prediction.get("evidence"), list)
        else {}
    )
    critical = set(golden.get("critical_fields", []))

    rows: list[dict[str, Any]] = []
    for field, expected in golden["fields"].items():
        if not _diekstrak(field):
            continue
        predicted = pred_flat.get(field)
        verdict, method = compare(expected, predicted)
        status = evidence.get(field, {}).get("status")
        rows.append(
            {
                "field": field,
                "expected": expected,
                "predicted": predicted,
                "verdict": verdict,
                "method": method,
                "critical": field in critical,
                "status": status,
                "false_acceptance": verdict in ("wrong", "spurious")
                and status in VERIFIED_STATUSES,
            }
        )

    def _acc(subset: list[dict[str, Any]], lenient: bool) -> float | None:
        if not subset:
            return None
        ok = {"exact", "correct_null"} | ({"lenient"} if lenient else set())
        return round(sum(r["verdict"] in ok for r in subset) / len(subset), 3)

    crit_rows = [r for r in rows if r["critical"]]
    items = (
        prediction.get("data", {}).get("List Item/Barang")
        or prediction.get("data", {}).get("Daftar Penawaran Harga")
        or []
    )
    return {
        "document": golden["document"],
        "field_count": len(rows),
        "accuracy_exact": _acc(rows, lenient=False),
        "accuracy_lenient": _acc(rows, lenient=True),
        "critical_accuracy_lenient": _acc(crit_rows, lenient=True),
        "missing": sum(r["verdict"] == "missing" for r in rows),
        "wrong": sum(r["verdict"] == "wrong" for r in rows),
        "false_acceptance_count": sum(r["false_acceptance"] for r in rows),
        "item_count_expected": golden.get("expected_item_count"),
        "item_count_predicted": len(items),
        "validation_status": (prediction.get("validation") or {}).get("status"),
        "llm_calls": (prediction.get("run_info") or {}).get("llm_calls"),
        "rows": rows,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Rata-rata HANYA dari golden yang sudah dikoreksi manusia. Golden rangka otomatis berisi
    tebakan mesin itu sendiri: skornya mendekati 1,0 dan dulu ikut mengangkat rata-rata
    (eval 2026-09-29: 0,913 dengan 2 rangka, padahal tanpa keduanya lebih rendah).
    Rata-rata field kritis hanya dari dokumen yang punya field kritis -- dokumen tanpa field
    kritis dulu dihitung 0 dan menurunkan rata-rata.
    """
    sah = [r for r in results if r.get("golden_terverifikasi", True)]
    kritis = [
        r["critical_accuracy_lenient"] for r in sah if r["critical_accuracy_lenient"] is not None
    ]
    return {
        "documents": len(sah),
        "documents_excluded": len(results) - len(sah),
        "mean_accuracy_lenient": round(sum(r["accuracy_lenient"] for r in sah) / len(sah), 3)
        if sah
        else None,
        "mean_critical_accuracy": round(sum(kritis) / len(kritis), 3) if kritis else None,
        "total_false_acceptance": sum(r["false_acceptance_count"] for r in sah),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate extraction outputs against golden files")
    parser.add_argument("--pred-dir", default=str(DEFAULT_PRED_DIR))
    parser.add_argument("--label", default=None, help="Simpan report ke eval/reports/<label>.json")
    parser.add_argument(
        "--verbose", action="store_true", help="Tampilkan semua field, bukan hanya yang salah"
    )
    args = parser.parse_args()

    pred_dir = Path(args.pred_dir)
    results = []
    belum_dikoreksi = []
    for golden_path in sorted(GOLDEN_DIR.glob("*.json")):
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        # Rangka dari scripts/buat_golden.py lahir dengan flag ini. Selama belum dicabut,
        # isinya masih tebakan mesin -- mengevaluasinya berarti membandingkan mesin dengan
        # dirinya sendiri, yang selalu mendekati 100% dan tidak membuktikan apa pun.
        if golden.get("belum_dikoreksi"):
            belum_dikoreksi.append(golden_path.name)
        pred_path = pred_dir / f"{Path(golden['document']).stem}.extract.json"
        if not pred_path.exists():
            print(f"⚠️  Prediksi tidak ditemukan: {pred_path}")
            continue
        result = evaluate_document(golden, json.loads(pred_path.read_text(encoding="utf-8")))
        result["golden_terverifikasi"] = not golden.get("belum_dikoreksi")
        results.append(result)

        print(f"\n📄 {result['document']}")
        print(
            f"   accuracy exact={result['accuracy_exact']}  lenient={result['accuracy_lenient']}  "
            f"critical={result['critical_accuracy_lenient']}  missing={result['missing']}  "
            f"wrong={result['wrong']}  "
            f"false_accept={result['false_acceptance_count']}  "
            f"items={result['item_count_predicted']}/{result['item_count_expected']}"
        )
        for row in result["rows"]:
            if args.verbose or row["verdict"] in ("wrong", "missing", "spurious"):
                flag = "❗" if row["critical"] else "  "
                print(
                    f"   {flag}[{row['verdict']:<8}] {row['field']}: "
                    f"expected={str(row['expected'])[:60]!r} got={str(row['predicted'])[:60]!r}"
                )

    if belum_dikoreksi:
        print(
            f"\n⚠️  {len(belum_dikoreksi)} berkas golden MASIH RANGKA OTOMATIS, nilainya belum "
            f"dikoreksi manual:"
        )
        for nama in belum_dikoreksi:
            print(f"      - {nama}")
        print(
            "   Berkas itu TIDAK ikut dihitung di rata-rata. Koreksi nilainya terhadap PDF "
            'asli, lalu hapus baris "belum_dikoreksi".'
        )

    if not results:
        print("Tidak ada dokumen yang dievaluasi.")
        return 1

    summary = summarize(results)
    summary.update(
        {
            # Ikut disimpan di report supaya jelas golden mana yang belum dihitung.
            "golden_belum_dikoreksi": belum_dikoreksi,
        }
    )
    print(f"\n📊 SUMMARY {summary}")

    if args.label:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"{args.label}.json"
        out.write_text(
            json.dumps(
                {
                    "label": args.label,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "pred_dir": str(pred_dir),
                    "summary": summary,
                    "results": results,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )
        print(f"💾 Report disimpan: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
