"""
Membandingkan dol-parser dengan sistem lain (mis. Landing AI) pada golden yang sama.

Perbandingan pertama 14 Okt 2026 memihak dol-parser: golden mengikuti konvensi prompt
dol-parser, satu golden berasal dari keluaran Landing AI, dan field konvensi dicampur dengan
field intrinsik. Tes ini menjaga aturan pembandingnya. Nilai rekaan.
"""

import json

from eval.run_eval import banding_sistem, jenis_field


def _golden(stem, fields, **lain):
    return {"document": f"{stem}.pdf", "fields": fields, "critical_fields": [], **lain}


def _tulis(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def test_field_konvensi_dipisah_dari_intrinsik():
    assert jenis_field("Catatan Khusus") == "konvensi"
    assert jenis_field("Pihak Pertama.Jabatan") == "konvensi"
    assert jenis_field("Nomor Kontrak Kerja") == "intrinsik"
    assert jenis_field("List Item/Barang[0].Harga Satuan") == "intrinsik"


def test_golden_turunan_dan_rangka_tidak_ikut_dibandingkan(tmp_path):
    pred, lain = tmp_path / "pred", tmp_path / "lain"
    pred.mkdir()
    lain.mkdir()
    fields = {"Nomor Kontrak Kerja": "12/AB/2031", "Catatan Khusus": "garansi satu tahun"}
    for stem in ("a", "b", "c"):
        _tulis(pred / f"{stem}.extract.json", {"data": {"Nomor Kontrak Kerja": "12/AB/2031"}})
    _tulis(
        lain / "a.json",
        {"Nomor Kontrak Kerja": "12/AB/2031", "Catatan Khusus": "garansi satu tahun"},
    )
    _tulis(lain / "berkas-lain.json", {"Nomor Kontrak Kerja": "salah"})
    _tulis(lain / "_peta.json", {"b": "berkas-lain.json"})

    hasil = banding_sistem(
        [
            _golden("a", fields),
            _golden("b", fields, turunan_dari="landingai"),
            _golden("c", fields, belum_dikoreksi=True),
        ],
        pred,
        lain,
        "landingai",
    )

    assert [d["document"] for d in hasil["dokumen"]] == ["a.pdf"]
    assert len(hasil["dilewati"]) == 2
    r = hasil["ringkasan"]
    assert r["dol-parser/intrinsik"] == {"benar": 1, "n": 1, "akurasi": 1.0}
    assert r["dol-parser/konvensi"]["benar"] == 0
    assert r["landingai/konvensi"]["benar"] == 1
    assert hasil["dokumen"][0]["benar_hanya_landingai"] == ["Catatan Khusus"]


def test_respons_api_ade_dibaca_dari_kunci_extraction(tmp_path):
    pred, lain = tmp_path / "pred", tmp_path / "lain"
    pred.mkdir()
    lain.mkdir()
    _tulis(pred / "a.extract.json", {"data": {"Nomor Kontrak Kerja": "12/AB/2031"}})
    _tulis(lain / "a.json", {"extraction": {"Nomor Kontrak Kerja": "12/AB/2031"}, "metadata": {}})

    hasil = banding_sistem(
        [_golden("a", {"Nomor Kontrak Kerja": "12/AB/2031"})], pred, lain, "landingai"
    )

    assert hasil["ringkasan"]["landingai/intrinsik"]["benar"] == 1


def test_dokumen_dengan_skema_berbeda_dilewati(tmp_path):
    """Kasus nyata: keluaran Landing AI untuk satu kontrak memakai skema berbahasa Inggris.
    Semua field-nya dihitung salah padahal tidak ada yang dibandingkan."""
    pred, lain = tmp_path / "pred", tmp_path / "lain"
    pred.mkdir()
    lain.mkdir()
    _tulis(pred / "a.extract.json", {"data": {"Nomor Kontrak Kerja": "12/AB/2031", "Lokasi": "X"}})
    _tulis(lain / "a.json", {"contract_numbers": [{"value": "12/AB/2031"}], "agreement_place": "X"})

    hasil = banding_sistem(
        [_golden("a", {"Nomor Kontrak Kerja": "12/AB/2031", "Lokasi": "X"})],
        pred,
        lain,
        "landingai",
    )

    assert hasil["dokumen"] == []
    assert "skema berbeda" in hasil["dilewati"][0]
