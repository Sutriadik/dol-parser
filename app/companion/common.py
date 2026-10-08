"""
Open ADE — Alat bersama untuk pemeta companion (kontrak, SPH, BAST).

Sebelumnya tiap pemeta punya salinan sendiri, dan salinan itu sudah berbeda diam-diam:
- identitas dokumen: pemeta memakai sha1 berkas, sementara `document_id` yang dibalas ke n8n
  memakai sha256 -- keduanya tidak pernah cocok, jadi n8n tidak bisa menemukan baris NocoDB
  dari `document_id` yang ia pegang;
- angka: `_num` hanya mengenal format Indonesia ("533,218,400" -> None), padahal
  `parse_id_number` di extractors sudah menangani format Indonesia dan Inggris.
Satu modul, satu perilaku.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from app.config import config
from app.document_ir.adapter import content_hash
from app.extractors.deterministic.numbers import parse_id_number

# Status bukti pipeline (app/schemas/evidence.FieldStatus) -> pilihan nilai di dol_schema.
# Kata "verified" sengaja tidak dibawa: sistem hanya memeriksa keberadaan nilai di dokumen.
STATUS_KE_SKEMA = {
    "AUTO_VERIFIED": "bukti_kuat",
    "AUTO_ACCEPTED": "bukti_cukup",
    "REVIEW_REQUIRED": "perlu_dicek",
    "UNSUPPORTED": "tidak_ada_di_dokumen",
    "CONFLICT": "bertentangan",
    "MISSING": "kosong",
}

# Field yang dibaca LLM tapi sengaja TIDAK disimpan maupun ditampilkan ke PM.
#   NPWP (2026-10-04): tidak dipakai BAST maupun verifikasi PM. Tetap diminta di prompt karena
#   menghapusnya dari prompt terbukti menurunkan akurasi field lain (lenient 0,884 -> 0,858,
#   nilai salah yang lolos 4 -> 8 pada golden yang sama).
FIELD_TIDAK_DISIMPAN = ("NPWP",)


def disimpan(field_path: str) -> bool:
    return field_path.rsplit(".", 1)[-1] not in FIELD_TIDAK_DISIMPAN


# "Jenis Biaya" dari pembaca tabel (app/parsers/table_extractor.jenis_biaya_baris) -> pilihan
# nilai dol_schema. Nilai lain (mis. dari jalur cadangan LLM) dikirim kosong, bukan ditebak.
_JENIS_BIAYA = {"OTC": "otc", "MRC": "mrc", "OTC dan MRC": "otc_dan_mrc"}


def jenis_biaya(v: Any) -> str | None:
    return _JENIS_BIAYA.get(txt(v) or "")


_BULAN = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}
_SATUAN = {
    "nol": 0,
    "satu": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
    "sembilan": 9,
}


def document_key(result: dict[str, Any], pdf_path: Path | None = None) -> str:
    """
    Kunci anti-dobel `document.content_hash` = `run_info.document_id` = sidik isi berkas.

    Berkas asli lebih dulu (dihitung ulang dari byte-nya); bila tidak ada, `document_id`
    dari pipeline -- yang juga dihitung dari byte berkas yang sama (engine.process_full).
    Tidak ada cadangan lain: kunci yang dikarang dari nama berkas membuat baris kembar.
    """
    if pdf_path and Path(pdf_path).exists():
        return content_hash(pdf_path)
    doc_id = (result.get("run_info") or {}).get("document_id")
    if not doc_id:
        raise ValueError(
            f"{result.get('document_name')}: tidak ada berkas asli maupun run_info.document_id; "
            f"identitas dokumen tidak bisa ditentukan"
        )
    return str(doc_id)


def txt(v: Any) -> str | None:
    if v is None or isinstance(v, (list, dict)):
        return None
    s = str(v).strip()
    return s or None


def num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return parse_id_number(v)


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_angka_kata(teks: str) -> int | None:
    """
    Bilangan Indonesia berupa kata -> int. Cukup untuk tanggal: hari 1-31, tahun 2000-2099.

    Diperlukan karena 3 dari 11 BAST menulis tanggal HANYA dalam kata, tanpa angka
    pembanding dalam kurung ("tanggal Dua Puluh Dua bulan Juni tahun Dua Ribu Dua Puluh Enam").
    Tanpa ini, tanggal dokumen-dokumen itu selalu NULL.
    """
    kata = [w for w in re.split(r"[\s-]+", teks.lower().strip()) if w]
    if not kata:
        return None
    # Tiga penampung, karena satu penampung tidak cukup: pengali ("puluh") memakai angka
    # SEBELUMNYA, sedangkan sisa kelompok ("dua" pada "dua puluh dua") harus DITAMBAHKAN.
    # Versi pertama hanya punya `sekarang` sehingga "dua puluh dua" terbaca 2, bukan 22.
    total = 0  # kelompok yang sudah ditutup oleh "ribu"
    grup = 0  # kelompok berjalan (< 1000) yang sudah pasti
    sekarang = 0  # satuan yang belum tahu akan dikali apa
    for w in kata:
        if w == "se":  # jaga-jaga bila terpisah spasi
            sekarang = 1
        elif w in _SATUAN:
            sekarang = _SATUAN[w]
        elif w == "sepuluh":
            grup += 10
            sekarang = 0
        elif w == "sebelas":
            grup += 11
            sekarang = 0
        elif w == "belas":  # "tiga belas" = 13
            grup += 10 + sekarang
            sekarang = 0
        elif w == "puluh":  # "dua puluh" = 20, satuan berikutnya ditambahkan
            grup += (sekarang or 1) * 10
            sekarang = 0
        elif w == "ratus":
            grup += (sekarang or 1) * 100
            sekarang = 0
        elif w == "seratus":
            grup += 100
            sekarang = 0
        elif w in ("ribu", "seribu"):
            total += (grup + sekarang or 1) * 1000
            grup = sekarang = 0
        else:
            return None  # kata asing -> jangan menebak
    return (total + grup + sekarang) or None


def parse_indonesian_date(text: str | None) -> date | None:
    """
    Kembalikan date bila teks bisa dibaca dengan yakin, selain itu None.
    Sengaja konservatif: lebih baik NULL daripada tanggal yang salah tafsir -- teks aslinya
    tetap tersimpan di kolom `*_text` sebagai bukti.
    """
    if not text:
        return None
    t = str(text).strip().lower()

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)  # 2026-08-11
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _safe_date(y, mo, d)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", t)  # 11/08/2026
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return _safe_date(y, mo, d)

    m = re.search(r"\b(\d{1,2})\s+([a-z]+)\s+(\d{4})\b", t)  # 11 Agustus 2026
    if m and m.group(2) in _BULAN:
        return _safe_date(int(m.group(3)), _BULAN[m.group(2)], int(m.group(1)))

    # Seluruhnya kata: "tanggal Dua Puluh Dua bulan Juni tahun Dua Ribu Dua Puluh Enam"
    m = re.search(r"tanggal\s+([a-z\s]+?)\s+bulan\s+([a-z]+)\s+tahun\s+([a-z\s]+)", t)
    if m and m.group(2) in _BULAN:
        hari = parse_angka_kata(m.group(1))
        tahun = parse_angka_kata(m.group(3))
        if hari and tahun and 1 <= hari <= 31 and 2000 <= tahun <= 2099:
            return _safe_date(tahun, _BULAN[m.group(2)], hari)
    return None


def is_our_org(name: str | None) -> bool:
    """Apakah nama instansi ini perusahaan kita (BUT)? Pola di config.OWN_COMPANY_PATTERNS."""
    if not name:
        return False
    return any(re.search(p, name, re.I) for p in config.OWN_COMPANY_PATTERNS)


def validation_notes(validation: dict[str, Any]) -> str | None:
    """Peringatan validasi -> teks untuk `document.validation_notes` (satu baris per peringatan)."""
    issues = [i for i in (validation or {}).get("issues") or [] if isinstance(i, dict)]
    lines = [f"[{i.get('severity')}] {i.get('message')}" for i in issues if i.get("message")]
    return "\n".join(lines) or None


def document_row(result: dict[str, Any], doc_key: str, doc_type: str) -> dict[str, Any]:
    run = result.get("run_info") or {}
    return {
        "content_hash": doc_key,
        "doc_type": doc_type,
        "source_filename": result.get("document_name") or "(tanpa nama)",
        "page_count": run.get("page_count") or None,
        "markdown": result.get("markdown") or None,
        "validation_notes": validation_notes(result.get("validation") or {}),
    }


def extracted_field_rows(result: dict[str, Any], doc_key: str) -> list[dict[str, Any]]:
    """Satu baris per field hasil ekstraksi, nama field persis seperti di skema ekstraksi."""
    rows = []
    for ev in result.get("evidence") or []:
        if not isinstance(ev, dict) or not ev.get("field") or not disimpan(str(ev["field"])):
            continue
        status = str(ev.get("status") or "MISSING").upper()
        rows.append(
            {
                "_document_ref": doc_key,
                "field_path": str(ev["field"]),
                "ai_value_text": txt(ev.get("value")),
                "evidence_page": ev.get("page"),
                "evidence_quote": txt(ev.get("evidence_text")),
                "evidence_score": ev.get("evidence_score"),
                "system_status": STATUS_KE_SKEMA.get(status, "perlu_dicek"),
            }
        )
    return rows


def extraction_run_row(result: dict[str, Any], doc_key: str, schema_version: str) -> dict[str, Any]:
    run = result.get("run_info") or {}
    timings = run.get("timings") or {}
    return {
        "_document_ref": doc_key,
        "ocr_engine": run.get("parser_engine"),
        "llm_model": run.get("model"),
        "prompt_version": run.get("prompt_version"),
        "schema_version": schema_version,
        "llm_call_count": run.get("llm_calls"),
        "parse_seconds": timings.get("parse_s"),
        "extract_seconds": timings.get("extract_s"),
        "validation_status": (result.get("validation") or {}).get("status"),
        "started_at": run.get("timestamp"),
    }


def evidence_for(result: dict[str, Any], field: str) -> dict[str, Any] | None:
    return next(
        (
            e
            for e in result.get("evidence") or []
            if isinstance(e, dict) and e.get("field") == field
        ),
        None,
    )
