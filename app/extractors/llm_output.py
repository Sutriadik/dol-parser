"""
Open ADE — Merapikan bentuk permintaan & keluaran LLM, tanpa memanggil LLM.

Fungsi murni: batas panjang di skema JSON yang dikirim ke Ollama, pembuang deret angka
sisa perulangan model, pengenal nilai placeholder, dan hasil kosong yang tetap valid.
Dipindah dari ollama_client.py (isi tidak berubah) supaya bisa diuji & dibaca terpisah.
"""

import re
from typing import Any

from pydantic import BaseModel

from app.logger import logger

PLACEHOLDER_PATTERNS = (
    "informasi tidak",
    "tidak tersedia",
    "tidak disebutkan",
    "tidak disediakan",
    "tidak ada",
    "tidak ditemukan",
    "tidak diketahui",
    "belum tersedia",
    "information not",
    "not available",
    "not found",
    "n/a",
)


# Batas panjang field teks di skema format Ollama (lihat _cap_string_lengths). Longgar
# terhadap nilai nyata -- terpanjang yang pernah terekstrak: 1.208 karakter (Catatan
# Khusus) -- tapi cukup ketat supaya perulangan tak berujung terpotong di satu field saja.
# Deret angka tanpa pemisah >= 20 digit: sisa perulangan LLM (lihat _strip_runaway_digits).
RUNAWAY_DIGITS = re.compile(r"[\s,;-]*\d{20,}")


TEXT_MAX_CHARS = 600


LONG_TEXT_MAX_CHARS = 2500


LONG_TEXT_FIELDS = {
    "Catatan Khusus",
    "Uraian Jaminan",
    "Keterangan",
    "Syarat dan Ketentuan",
    "Mekanisme Skema Pembayaran",
    "Ketentuan Pembayaran",
    "Informasi Bea Meterai",
    "Jangka Waktu",
    "Persentase Sanksi/Penalti",
    "Garansi",
    "Garansi / SLA",
    "Syarat Lampiran Wajib BAST",
    "Pernyataan Penerimaan",
    "Hasil Uji Terima Keseluruhan",
    "Spesifikasi",
    "Nama Dokumen",
}


def is_placeholder(value: Any) -> bool:
    """Nilai kosong atau kalimat penolakan LLM ("tidak disebutkan") dianggap null."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    return v in ("", "null", "none", "-", "[]") or any(p in v for p in PLACEHOLDER_PATTERNS)


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == 0.0 or value == [] or is_placeholder(value)


def slim_schema(schema_class: type, drop_fields: tuple[str, ...]) -> dict[str, Any]:
    """JSON-schema tanpa field yang diisi deterministik, agar LLM tidak mengetik ulang tabel."""
    schema = schema_class.model_json_schema()
    props = schema.get("properties", {})
    for alias in drop_fields:
        props.pop(alias, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in drop_fields]
    return schema


def cap_string_lengths(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Beri `maxLength` pada setiap field teks di skema format Ollama.

    Ollama menegakkan skema lewat grammar, jadi batas ini benar-benar memotong keluaran.
    Tanpanya satu field bisa berulang tanpa henti: pada kontrak pindaian 16 halaman model
    menulis alamat lalu "0812345678901234567890..." sampai kuota 4.096 token habis, JSON
    terpotong, dan seluruh dokumen gagal setelah 325 detik. Batas diambil dari 52 hasil
    ekstraksi nyata: 210 dari 223 field tidak pernah melebihi 200 karakter.
    """

    def cap(node: Any, name: str = "") -> None:
        if isinstance(node, dict):
            if node.get("type") == "string" and "maxLength" not in node:
                node["maxLength"] = (
                    LONG_TEXT_MAX_CHARS if name in LONG_TEXT_FIELDS else TEXT_MAX_CHARS
                )
            for key, sub in node.items():
                if key == "properties" and isinstance(sub, dict):
                    for prop, prop_schema in sub.items():
                        cap(prop_schema, prop)
                else:
                    cap(sub, name)  # anyOf / items / $defs mewarisi nama field
        elif isinstance(node, list):
            for sub in node:
                cap(sub, name)

    cap(schema)
    return schema


def strip_runaway_digits(data: Any) -> Any:
    """
    Buang deret angka tanpa pemisah >= 20 digit dari setiap nilai teks.

    Batas panjang skema menghentikan perulangan, tapi sisanya tetap tertulis sampai batas
    itu ("... Indonesia, 0812345678901234567890..."). Tidak ada nomor dokumen Indonesia
    sepanjang itu tanpa pemisah (NIK 16 digit, rekening 13-16, NPWP bertitik), jadi deret
    seperti ini pasti sisa perulangan -- bukan data.
    """
    if isinstance(data, dict):
        return {k: strip_runaway_digits(v) for k, v in data.items()}
    if isinstance(data, list):
        return [strip_runaway_digits(v) for v in data]
    if isinstance(data, str) and RUNAWAY_DIGITS.search(data):
        cleaned = RUNAWAY_DIGITS.sub("", data).strip(" ,;-")
        logger.warning(f"⚠️  Deret angka perulangan LLM dibuang: {data[:60]!r}...")
        return cleaned or None
    return data


def empty_result(schema_class: type) -> BaseModel:
    """Hasil tanpa isi yang tetap lolos validasi: objek wajib jadi objek kosong."""

    def empty(model: type) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name, info in model.model_fields.items():
            if not info.is_required():
                continue
            args = getattr(info.annotation, "__args__", None) or (info.annotation,)
            sub = next((t for t in args if isinstance(t, type) and issubclass(t, BaseModel)), None)
            if sub:
                out[info.alias or name] = empty(sub)
            elif info.annotation is str:
                out[info.alias or name] = ""  # teks wajib non-null; dianggap kosong
            elif getattr(info.annotation, "__origin__", None) is list:
                out[info.alias or name] = []
            else:
                out[info.alias or name] = None
        return out

    return schema_class.model_validate(empty(schema_class))


# ------------------------------------------------------------------ targeted clause
def only_null_keys(templates: dict[str, Any], null_fields: list[str]) -> dict[str, Any]:
    """
    Buang kunci template yang sudah terisi, tapi HANYA di level teratas.

    Objek bersarang (mis. "Pihak Pertama") dikirim utuh walau cuma satu sub-field yang
    null: memotongnya jadi satu sub-field saja membuat model kehilangan penanda untuk
    membedakan Pihak Pertama dan Pihak Kedua, dan "Nama Representative" kedua pihak
    jadi gagal terisi. Hematnya sedikit, ruginya besar.
    """
    return {
        key: value
        for key, value in templates.items()
        if key in null_fields or any(f.startswith(f"{key}.") for f in null_fields)
    }
