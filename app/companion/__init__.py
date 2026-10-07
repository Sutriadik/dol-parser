"""
Pemeta hasil ekstraksi -> tabel dol-schema, pusher NocoDB, dan penyusun draf BAST.

Paket ini bergantung pada repo dol-schema yang dipasang dari folder sejajar
(`pip install -e ../dol-schema`). Versinya tidak dikunci pip, jadi diperiksa di sini, saat
paket di-import: versi lain berarti nama kolom, pilihan nilai, atau tabel bisa berbeda dari
yang ditulis pemeta -- dan baru ketahuan ketika data gagal masuk NocoDB.
"""

from app.config import config


class SkemaTidakCocok(RuntimeError):
    """dol-schema yang terpasang bukan versi yang dipahami kode ini."""


def cek_versi_skema(terpasang: str, diharapkan: str) -> None:
    if terpasang != diharapkan:
        raise SkemaTidakCocok(
            f"dol-schema terpasang versi {terpasang}, kode ini butuh {diharapkan}.\n"
            f"  - Bila folder ../dol-schema tertinggal: cd ../dol-schema && git pull "
            f"(atau git checkout {diharapkan})\n"
            f"  - Bila dol-schema sengaja dinaikkan: sesuaikan pemeta di app/companion/, lalu "
            f"ubah DOL_SCHEMA_VERSION di app/config.py"
        )


def _versi_terpasang() -> str:
    from dol_schema import SCHEMA_VERSION

    return SCHEMA_VERSION


cek_versi_skema(_versi_terpasang(), config.DOL_SCHEMA_VERSION)
