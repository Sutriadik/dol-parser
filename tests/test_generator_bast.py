"""
Penyusun draf BAST (app/companion/generator.py): hanya dari nilai yang dikonfirmasi PM.

BAST pelanggan memicu tagihan; salah pihak, salah kota, atau "Baik dan Lengkap" sebelum uji
berujung tagihan ditolak (briefing hlm. 10). Tes ini menjaga supaya generator menolak
menyusun draf alih-alih mengisi kekosongan dengan tebakan.
"""

from app.companion.contract_mapper import map_contract
from app.companion.generator import hitung_nilai_terverifikasi, susun_draf_bast
from tests.test_companion_contract import _sample_contract_extraction

_ITEM = "List Item/Barang[{i}].{f}"


def _kontrak_dari_db():
    """Baris seperti dibaca dari database (nama teknis), tanpa placeholder ref."""
    p = map_contract(_sample_contract_extraction())

    def bersih(rows):
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]

    return (
        bersih(p["contract"])[0],
        bersih(p["contract_party"]),
        bersih(p["contract_item"]),
        bersih(p["contract_requirement"]),
    )


def _semua_dikonfirmasi():
    raw = _sample_contract_extraction()["data"]
    v = {
        "Nomor Kontrak Kerja": raw["Nomor Kontrak Kerja"],
        "Nama Pekerjaan": raw["Nama Pekerjaan"],
        "Tanggal Pembuatan Dokumen": raw["Tanggal Pembuatan Dokumen"],
        "Total Harga Pekerjaan": "150000000",
    }
    for dasar in ("Pihak Pertama", "Pihak Kedua"):
        for f in ("Nama Perusahaan", "Nama Representative", "Jabatan"):
            v[f"{dasar}.{f}"] = raw[dasar][f]
    for i, item in enumerate(raw["List Item/Barang"]):
        v[_ITEM.format(i=i, f="Deskripsi Item/Barang/Pekerjaan")] = item[
            "Deskripsi Item/Barang/Pekerjaan"
        ]
        v[_ITEM.format(i=i, f="volume")] = str(item["volume"])
        v[_ITEM.format(i=i, f="unit")] = item["unit"]
        v[_ITEM.format(i=i, f="Harga Satuan")] = str(item["Harga Satuan"])
    for i, s in enumerate(raw["Syarat Lampiran Wajib BAST"]):
        v[f"Syarat Lampiran Wajib BAST[{i}]"] = s
    return v


def _susun(v, **kw):
    kw.setdefault("kota", "Kota Contoh")
    kw.setdefault("tanggal_serah_terima", "2026-02-15")
    return susun_draf_bast(*_kontrak_dari_db(), v, **kw)


# nilai terverifikasi
def test_nilai_terverifikasi_sama_dengan_view_postgresql():
    ef = [
        {"field_path": "A", "ai_value_text": "1"},
        {"field_path": "B", "ai_value_text": "2"},
        {"field_path": "C", "ai_value_text": "3"},
        {"field_path": "D", "ai_value_text": "4 baru"},
    ]
    rv = [
        {"field_path": "A", "decision": "benar", "reviewed_ai_value_text": "1"},
        {
            "field_path": "B",
            "decision": "dikoreksi",
            "reviewed_ai_value_text": "2",
            "final_value_text": "2,5",
        },
        {"field_path": "C", "decision": "ditolak", "reviewed_ai_value_text": "3"},
        {"field_path": "D", "decision": "benar", "reviewed_ai_value_text": "4 lama"},
    ]
    assert hitung_nilai_terverifikasi(ef, rv) == {"A": "1", "B": "2,5"}


# menolak
def test_tanpa_konfirmasi_pm_tidak_ada_draf():
    draf, masalah = _susun({})
    assert draf is None
    assert "belum dikonfirmasi PM: Nomor Kontrak Kerja" in masalah
    assert any("Harga Satuan" not in m for m in masalah)  # harga tidak diminta tanpa template harga


def test_kota_dan_tanggal_tidak_ditebak():
    draf, masalah = _susun(_semua_dikonfirmasi(), kota=None, tanggal_serah_terima=None)
    assert draf is None
    assert "kota serah terima belum diisi PM" in masalah
    assert "tanggal serah terima belum diisi PM" in masalah


def test_satu_item_belum_dikonfirmasi_menggagalkan_draf():
    v = _semua_dikonfirmasi()
    del v[_ITEM.format(i=2, f="volume")]
    draf, masalah = _susun(v)
    assert draf is None and masalah == [f"belum dikonfirmasi PM: {_ITEM.format(i=2, f='volume')}"]


# menyusun
def test_draf_lengkap_dari_nilai_terkonfirmasi():
    draf, masalah = _susun(_semua_dikonfirmasi())
    assert masalah == []
    assert draf["kepala"]["nomor_kontrak"] == "123/00/XYZ-01/BUT/2026"
    assert draf["kepala"]["kota_serah_terima"] == "Kota Contoh"
    # BUT (pelaksana) menyerahkan, pelanggan (pemberi kerja) menerima.
    assert draf["penyerah"]["nama_instansi"] == "PT Bhakti Unggul Teknovasi"
    assert draf["penerima"]["nama_instansi"] == "DPMPTSP Kabupaten Contoh"
    assert [b["no"] for b in draf["rincian"]] == [1, 2, 3]
    assert all(b["hasil_uji"] is None for b in draf["rincian"])  # belum diuji = kosong
    assert all(b["harga_satuan"] is None for b in draf["rincian"])
    assert draf["lampiran_wajib"] == ["Surat Jalan / Delivery Order", "Berita Acara Uji Terima"]


def test_koreksi_pm_yang_dipakai_bukan_nilai_sistem():
    v = _semua_dikonfirmasi()
    v["Nomor Kontrak Kerja"] = "123/00/XYZ-01/BUT/2026-REV"  # PM mengoreksi
    draf, _ = _susun(v)
    assert draf["kepala"]["nomor_kontrak"].endswith("-REV")


def test_serah_terima_parsial_memakai_volume_yang_diserahkan():
    draf, _ = _susun(_semua_dikonfirmasi(), volume_diserahkan={1: 2})
    assert draf["rincian"][0]["volume"] == 2 and draf["rincian"][1]["volume"] == 8.0


def test_harga_hanya_bila_template_memuat_harga():
    draf, _ = _susun(_semua_dikonfirmasi(), sertakan_harga=True)
    assert draf["rincian"][0]["harga_satuan"] == 25000000.0
    assert draf["kepala"]["nilai_kontrak"] == 150000000.0
