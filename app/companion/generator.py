"""
Open ADE — Penyusun data draf BAST pelanggan dari kontrak yang SUDAH DIKONFIRMASI PM.

Fungsi murni: tidak membaca berkas, tidak memanggil NocoDB. Pemanggil (nanti: n8n atau
layanan render milik RPA) membaca baris dari database lalu menyerahkannya ke sini.

Aturan (briefing hlm. 9, 10, 20):
- **Hanya nilai yang dikonfirmasi PM.** Header, pihak, dan rincian diambil dari
  `nilai_terverifikasi` (keputusan PM `benar`/`dikoreksi` yang belum basi) -- bukan dari
  kolom tabel yang ditulis mesin. Nilai sistem yang belum diperiksa tidak dipakai.
- **Tidak ada nilai karangan.** Tidak ada kota bawaan, penandatangan bawaan, hasil uji
  bawaan, atau kalimat penerimaan bawaan. Yang kurang dilaporkan sebagai masalah; draf
  tidak disusun sampai semuanya lengkap. BAST pelanggan menahan tagihan -- guard-nya
  preventif penuh.
- **Peran dari tabel Pihak Kontrak**, bukan dari sebutan "Pihak Pertama/Kedua":
  pelaksana (BUT) menyerahkan, pemberi kerja menerima.
- **Serah terima parsial** didukung lewat `volume_diserahkan`; tanpa itu volume = kontrak.

Bentuk keluaran sengaja bukan baris tabel: tabel BAST masih usulan di dol_schema.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_PIHAK_FIELD = {"PIHAK PERTAMA": "Pihak Pertama", "PIHAK KEDUA": "Pihak Kedua"}
_ITEM = "List Item/Barang[{i}].{f}"
_WAJIB_KEPALA = ("Nomor Kontrak Kerja", "Nama Pekerjaan", "Tanggal Pembuatan Dokumen")


def hitung_nilai_terverifikasi(
    extracted_fields: Iterable[dict[str, Any]], reviews: Iterable[dict[str, Any]]
) -> dict[str, str]:
    """
    Sama persis dengan view PostgreSQL `nilai_terverifikasi` (dol-schema), untuk jalur
    NocoDB yang tidak punya view: {field_path: nilai} hanya untuk field yang diputuskan PM
    `benar`/`dikoreksi` dan keputusannya belum basi (nilai sistem tidak berubah sejak itu).
    """
    sistem = {ef["field_path"]: ef.get("ai_value_text") for ef in extracted_fields}
    hasil: dict[str, str] = {}
    for rv in reviews:
        fp = rv.get("field_path")
        if fp not in sistem or rv.get("decision") not in ("benar", "dikoreksi"):
            continue
        if sistem[fp] != rv.get("reviewed_ai_value_text"):
            continue  # basi: PM melihat nilai lain
        hasil[fp] = rv.get("final_value_text") if rv["decision"] == "dikoreksi" else sistem[fp]
    return hasil


def _angka(v: str | None) -> float | None:
    from app.extractors.deterministic.numbers import parse_id_number

    return parse_id_number(v) if v not in (None, "") else None


def susun_draf_bast(
    kontrak: dict[str, Any],
    pihak: list[dict[str, Any]],
    rincian: list[dict[str, Any]],
    syarat: list[dict[str, Any]],
    nilai_terverifikasi: dict[str, str],
    *,
    kota: str | None = None,
    tanggal_serah_terima: str | None = None,
    volume_diserahkan: dict[int, float] | None = None,
    sertakan_harga: bool = False,
) -> tuple[dict[str, Any] | None, list[str]]:
    """
    -> (draf, masalah). `draf` None bila `masalah` tidak kosong.

    Masukan memakai nama teknis dol_schema: `kontrak` = satu baris contract, `pihak` = baris
    contract_party, `rincian` = baris contract_item, `syarat` = baris contract_requirement.
    `kota` dan `tanggal_serah_terima` diisi PM saat menyusun BAST -- tidak ditebak.
    """
    v = nilai_terverifikasi
    masalah: list[str] = []

    def wajib(field: str) -> str | None:
        nilai = v.get(field)
        if nilai in (None, ""):
            masalah.append(f"belum dikonfirmasi PM: {field}")
        return nilai

    kepala = {
        "nomor_kontrak": wajib("Nomor Kontrak Kerja"),
        "nama_pekerjaan": wajib("Nama Pekerjaan"),
        "tanggal_kontrak": wajib("Tanggal Pembuatan Dokumen"),
        "nilai_kontrak": _angka(wajib("Total Harga Pekerjaan")) if sertakan_harga else None,
        "kota_serah_terima": kota,
        "tanggal_serah_terima": tanggal_serah_terima,
        "mata_uang": kontrak.get("currency") or "IDR",
    }
    if not kota:
        masalah.append("kota serah terima belum diisi PM")
    if not tanggal_serah_terima:
        masalah.append("tanggal serah terima belum diisi PM")

    peran_bast = {"pelaksana": "penyerah", "pemberi_kerja": "penerima"}
    pihak_bast: dict[str, dict[str, Any]] = {}
    for p in pihak:
        dasar = _PIHAK_FIELD.get((p.get("party_label_text") or "").upper())
        peran = peran_bast.get(p.get("role"))
        if not dasar or not peran:
            masalah.append(
                f"pihak kontrak tanpa sebutan/peran yang dikenali: {p.get('org_name_text')}"
            )
            continue
        pihak_bast[peran] = {
            "nama_instansi": wajib(f"{dasar}.Nama Perusahaan"),
            "nama_penandatangan": wajib(f"{dasar}.Nama Representative"),
            "jabatan": v.get(f"{dasar}.Jabatan"),
            "alamat": v.get(f"{dasar}.Alamat"),
        }
    for peran in ("penyerah", "penerima"):
        if peran not in pihak_bast:
            masalah.append(f"pihak {peran} tidak ada di data kontrak")

    baris = []
    for item in sorted(rincian, key=lambda r: r["line_no"]):
        i = item["line_no"] - 1
        volume = _angka(wajib(_ITEM.format(i=i, f="volume")))
        if volume_diserahkan and item["line_no"] in volume_diserahkan:
            volume = volume_diserahkan[item["line_no"]]
        baris.append(
            {
                "no": item["line_no"],
                "uraian": wajib(_ITEM.format(i=i, f="Deskripsi Item/Barang/Pekerjaan")),
                "volume": volume,
                "satuan": wajib(_ITEM.format(i=i, f="unit")),
                "harga_satuan": _angka(wajib(_ITEM.format(i=i, f="Harga Satuan")))
                if sertakan_harga
                else None,
                "hasil_uji": None,  # diisi setelah uji terima, bukan sebelumnya
            }
        )
    if not baris:
        masalah.append("kontrak tidak punya rincian pekerjaan")

    lampiran = []
    for s in syarat:
        if s.get("requirement_type") != "lampiran_wajib":
            continue
        lampiran.append(wajib(f"Syarat Lampiran Wajib BAST[{s['line_no'] - 1}]"))

    if masalah:
        return None, masalah
    return {
        "kepala": kepala,
        "penyerah": pihak_bast["penyerah"],
        "penerima": pihak_bast["penerima"],
        "rincian": baris,
        "lampiran_wajib": lampiran,
    }, []
