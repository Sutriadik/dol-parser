"""
Pemeta kontrak (app/companion/contract_mapper.py) -> tabel dol_schema yang berlaku.

Yang dijaga: hal yang kalau rusak tidak menimbulkan error, hanya data salah diam-diam --
peran pihak tertukar, nilai karangan masuk NocoDB, isian n8n terhapus saat proses ulang.
"""

import copy

import pytest

from app.companion.contract_mapper import map_contract
from dol_schema import validate_payload


def _sample_contract_extraction():
    return {
        "document_name": "SPK-Pengadaan-Switch-CCTV.pdf",
        "document_type": "contract",
        "data": {
            "Nomor Kontrak Kerja": "123/00/XYZ-01/BUT/2026",
            "Nomor Kontrak Internal": "02/SPK/DPMPTSP/2026",
            "Nama Pekerjaan": "Pengadaan Perangkat Switch Penunjang Pemasangan CCTV",
            "Tanggal Pembuatan Dokumen": "15 Januari 2026",
            "Jangka Waktu": "15 Januari 2026 s.d 15 Februari 2026",
            "Durasi Kerja": "30 hari kalender",
            "Total Harga Pekerjaan": 150000000,
            "sub total": 135135135,
            "Total PPN": 14864865,
            "persentase ppn": "11%",
            "Mekanisme Skema Pembayaran": "Pembayaran dilakukan 100% setelah BAST ditandatangani",
            "Persentase Sanksi/Penalti": "1/1000 per hari",
            "Syarat Lampiran Wajib BAST": [
                "Surat Jalan / Delivery Order",
                "Berita Acara Uji Terima",
            ],
            "Nama Bank": "Bank Mandiri",
            "Nomor Rekening Bank": "131-00-1234567-8",
            "Nama Rekening Bank": "PT Bhakti Unggul Teknovasi",
            "Lokasi": "Kota Contoh",
            "Pihak Pertama": {
                "Nama Perusahaan": "DPMPTSP Kabupaten Contoh",
                "Nama Representative": "Drs. Ahmad Hidayat, M.Si",
                "Jabatan": "Kepala Dinas DPMPTSP",
                "Alamat": "Jl. Contoh No. 10 Kota Contoh",
            },
            "Pihak Kedua": {
                "Nama Perusahaan": "PT Bhakti Unggul Teknovasi",
                "Nama Representative": "M. Dany Kurniawan",
                "Jabatan": "Direktur",
                "Alamat": "Bandung Technoplex",
            },
            "List Item/Barang": [
                {
                    "No": "1",
                    "Kategori/Kelompok": "Hardware",
                    "Deskripsi Item/Barang/Pekerjaan": "Switch Cisco Catalyst 24 Port Gigabit PoE+",
                    "Spesifikasi": "Model WS-C2960X-24PD-L",
                    "volume": 4.0,
                    "unit": "Unit",
                    "Harga Satuan": 25000000,
                    "Jumlah Harga": 100000000,
                    "Keterangan": "Garansi 1 Tahun",
                },
                {
                    "No": "2",
                    "Kategori/Kelompok": "Aksesoris",
                    "Deskripsi Item/Barang/Pekerjaan": "SFP Module 1Gbps Single Mode",
                    "Spesifikasi": "GLC-LH-SMD",
                    "volume": 8.0,
                    "unit": "Pcs",
                    "Harga Satuan": 2500000,
                    "Jumlah Harga": 20000000,
                    "Keterangan": "Original",
                },
                {
                    "No": "3",
                    "Kategori/Kelompok": "Jasa",
                    "Deskripsi Item/Barang/Pekerjaan": (
                        "Jasa Instalasi, Konfigurasi, dan Uji Fungsi Jaringan CCTV"
                    ),
                    "Spesifikasi": "Setting VLAN dan Trunking",
                    "volume": 1.0,
                    "unit": "Paket",
                    "Harga Satuan": 15135135,
                    "Jumlah Harga": 15135135,
                    "Keterangan": "Termasuk Dokumentasi",
                },
            ],
        },
        "evidence": [
            {
                "field": "Nomor Kontrak Kerja",
                "value": "123/00/XYZ-01/BUT/2026",
                "page": 1,
                "evidence_text": "Nomor: 123/00/XYZ-01/BUT/2026",
                "evidence_score": 0.98,
                "status": "auto_verified",
            },
            {
                "field": "Syarat Lampiran Wajib BAST[1]",
                "value": "Berita Acara Uji Terima",
                "page": 4,
                "evidence_text": "melampirkan Berita Acara Uji Terima yang ditandatangani",
                "evidence_score": 0.9,
                "status": "AUTO_ACCEPTED",
            },
            {
                "field": "Total Harga Pekerjaan",
                "value": "150000000",
                "page": 2,
                "evidence_text": "Rp 150.000.000,- (seratus lima puluh juta rupiah)",
                "evidence_score": 0.95,
                "status": "auto_verified",
            },
        ],
        "run_info": {
            "document_id": "doc-spk-contoh",
            "page_count": 5,
            "parser_engine": "rapidocr",
            "model": "gemini-2.5-flash",
            "timings": {"parse_s": 12.5, "extract_s": 4.2},
            "timestamp": "2026-09-28T10:00:00Z",
        },
        "markdown": "# SURAT PERINTAH KERJA (SPK)...",
    }


def _peran(payload):
    return {
        p["party_label_text"]: (p["role"], p["org_name_text"]) for p in payload["contract_party"]
    }


def test_payload_kontrak_hanya_tabel_berlaku_dan_lolos_validasi():
    payload = map_contract(_sample_contract_extraction())
    assert set(payload) == {
        "document",
        "contract",
        "contract_party",
        "contract_item",
        "contract_requirement",
        "contract_payment_term",
        "extracted_field",
        "extraction_run",
    }
    assert "bast_draft" not in payload  # tabel BAST ditunda; tidak dibuat di sini
    assert validate_payload(payload) == []


def test_header_kontrak_terpetakan():
    c = map_contract(_sample_contract_extraction())["contract"][0]
    assert c["contract_number"] == "123/00/XYZ-01/BUT/2026"
    assert c["contract_value"] == 150000000.0
    assert c["contract_date"] == "2026-01-15"
    assert c["start_date"] == "2026-01-15" and c["end_date"] == "2026-02-15"
    assert c["location_text"] == "Kota Contoh"
    assert c["bast_terms"] == "- Surat Jalan / Delivery Order\n- Berita Acara Uji Terima"


def test_but_di_pihak_kedua_adalah_pelaksana():
    assert _peran(map_contract(_sample_contract_extraction())) == {
        "PIHAK PERTAMA": ("pemberi_kerja", "DPMPTSP Kabupaten Contoh"),
        "PIHAK KEDUA": ("pelaksana", "PT Bhakti Unggul Teknovasi"),
    }


def test_but_di_pihak_pertama_tetap_pelaksana():
    """Sebutan Pihak Pertama/Kedua terbalik antar-format (pelajaran 14 BAST). Peran harus
    mengikuti identitas BUT, bukan posisinya."""
    raw = _sample_contract_extraction()
    d = raw["data"]
    d["Pihak Pertama"], d["Pihak Kedua"] = d["Pihak Kedua"], d["Pihak Pertama"]
    payload = map_contract(raw)
    assert _peran(payload)["PIHAK PERTAMA"] == ("pelaksana", "PT Bhakti Unggul Teknovasi")
    assert _peran(payload)["PIHAK KEDUA"][0] == "pemberi_kerja"
    assert payload["document"][0]["validation_notes"] is None


def test_peran_tidak_pasti_diberitahukan_ke_pm():
    raw = _sample_contract_extraction()
    raw["data"]["Pihak Kedua"]["Nama Perusahaan"] = "CV Mitra Lain"
    payload = map_contract(raw)
    assert "Peran pihak kontrak belum pasti" in payload["document"][0]["validation_notes"]
    assert validate_payload(payload) == []


def test_tidak_ada_nilai_karangan():
    """Nilai yang tidak terbaca dikirim kosong. Dulu: '(tidak terbaca)', nama berkas sebagai
    judul pekerjaan, dan pihak bernama 'Pemberi Kerja' -- lolos ke NocoDB seolah data."""
    raw = _sample_contract_extraction()
    del raw["data"]["Nama Pekerjaan"]
    del raw["data"]["Pihak Pertama"]["Nama Representative"]
    raw["data"]["Pihak Kedua"] = {}
    payload = map_contract(raw)
    assert payload["contract"][0]["work_title"] is None
    pihak = payload["contract_party"]
    assert len(pihak) == 1 and pihak[0]["signer_name"] is None
    assert validate_payload(payload) == []


def test_kolom_milik_n8n_tidak_dikirim():
    """mybhakti_*_ref diisi n8n. Mengirim None lewat PATCH menghapus isian n8n setiap kali
    dokumen diproses ulang."""
    payload = map_contract(_sample_contract_extraction())
    for tabel, rows in payload.items():
        for r in rows:
            assert not any(k.startswith("mybhakti_") for k in r), tabel


def test_syarat_lampiran_membawa_bukti_dan_indeks_asli():
    raw = _sample_contract_extraction()
    raw["data"]["Syarat Lampiran Wajib BAST"].insert(1, "  ")  # elemen kosong dari LLM
    raw["evidence"][1]["field"] = "Syarat Lampiran Wajib BAST[2]"
    syarat = map_contract(raw)["contract_requirement"]
    assert [s["line_no"] for s in syarat] == [1, 3]  # = indeks mentah + 1
    uji = syarat[1]
    assert uji["requirement_type"] == "lampiran_wajib"
    assert uji["evidence_page"] == 4 and "Uji Terima" in uji["evidence_quote"]


def test_angka_format_inggris_terbaca():
    raw = _sample_contract_extraction()
    raw["data"]["Total Harga Pekerjaan"] = "533,218,400"
    raw["data"]["List Item/Barang"][0]["Harga Satuan"] = "Rp 25.000.000,-"
    payload = map_contract(raw)
    assert payload["contract"][0]["contract_value"] == 533218400.0
    assert payload["contract_item"][0]["unit_price"] == 25000000.0


def test_status_dan_nama_field_untuk_pm():
    fields = {
        f["field_path"]: f for f in map_contract(_sample_contract_extraction())["extracted_field"]
    }
    assert fields["Nomor Kontrak Kerja"]["system_status"] == "bukti_kuat"
    assert fields["Syarat Lampiran Wajib BAST[1]"]["system_status"] == "bukti_cukup"


def test_kunci_dokumen_sama_dengan_document_id_untuk_n8n(tmp_path):
    raw = _sample_contract_extraction()
    assert map_contract(raw)["document"][0]["content_hash"] == "doc-spk-contoh"
    pdf = tmp_path / "spk.pdf"
    pdf.write_bytes(b"%PDF-1.4 uji")
    from app.document_ir.adapter import content_hash

    assert map_contract(raw, pdf_path=pdf)["document"][0]["content_hash"] == content_hash(pdf)


def test_tanpa_identitas_dokumen_ditolak():
    raw = copy.deepcopy(_sample_contract_extraction())
    raw["run_info"].pop("document_id")
    with pytest.raises(ValueError):
        map_contract(raw)


def test_npwp_dibaca_llm_tapi_tidak_disimpan():
    """Keputusan 2026-10-04: NPWP tidak dipakai BAST maupun verifikasi PM. LLM tetap
    memintanya (menghapusnya dari prompt menurunkan akurasi field lain), tapi tidak ada
    satu kolom pun -- termasuk antrean PM di Hasil Ekstraksi -- yang memuatnya."""
    raw = _sample_contract_extraction()
    raw["data"]["Pihak Pertama"]["NPWP"] = "12.345.678.9-012.345"
    raw["evidence"].append(
        {
            "field": "Pihak Pertama.NPWP",
            "value": "12.345.678.9-012.345",
            "page": 1,
            "evidence_score": 0.98,
            "status": "AUTO_VERIFIED",
        }
    )
    payload = map_contract(raw)
    for rows in payload.values():
        for r in rows:
            assert not any("npwp" in k for k in r)
    assert not any("NPWP" in f["field_path"] for f in payload["extracted_field"])


def test_eval_tidak_menghitung_field_yang_sengaja_dihapus():
    from eval.run_eval import evaluate_document

    golden = {
        "document": "k.pdf",
        "fields": {"Pihak Pertama.NPWP": "01.234", "Nomor Kontrak Kerja": "K/1"},
    }
    hasil = evaluate_document(golden, {"data": {"Nomor Kontrak Kerja": "K/1"}})
    assert [r["field"] for r in hasil["rows"]] == ["Nomor Kontrak Kerja"]


# --------------------------------------------------------------------- nasib setiap field
# Daftar ini adalah keputusan tertulis: setiap field yang diminta ke LLM berakhir di mana.
#   "kolom"           -> kolom tabel domain (contract, ...); umumnya juga di Hasil Ekstraksi
#   "hasil_ekstraksi" -> hanya baris di Hasil Ekstraksi (dilihat PM), tanpa kolom untuk n8n
#   "tidak_disimpan"  -> tidak sampai ke NocoDB sama sekali; hanya ada di *.extract.json
#
# Dua kegunaan:
# 1. Field yang diekstrak tapi diam-diam dibuang pemeta langsung terlihat.
# 2. field_path = alias Pydantic, dan keputusan PM (field_review) dikunci dengan field_path.
#    Mengganti nama alias membuat keputusan PM lama tidak lagi cocok dengan baris mana pun.
#    Tes ini gagal saat alias berubah, supaya perubahan itu disengaja, bukan kebetulan.
NASIB_KONTRAK = {
    "Pihak Pertama.Nama Perusahaan": "kolom",
    "Pihak Pertama.NPWP": "tidak_disimpan",  # FIELD_TIDAK_DISIMPAN, keputusan 2026-10-04
    "Pihak Pertama.Nama Representative": "kolom",
    "Pihak Pertama.Jabatan": "kolom",
    "Pihak Pertama.Alamat": "kolom",
    "Pihak Kedua.Nama Perusahaan": "kolom",
    "Pihak Kedua.NPWP": "tidak_disimpan",
    "Pihak Kedua.Nama Representative": "kolom",
    "Pihak Kedua.Jabatan": "kolom",
    "Pihak Kedua.Alamat": "kolom",
    "List Item/Barang[].Nomor Item": "tidak_disimpan",  # SKIP_KEYS; line_no = urutan baca
    "List Item/Barang[].Kategori/Kelompok": "kolom",
    "List Item/Barang[].Deskripsi Item/Barang/Pekerjaan": "kolom",
    "List Item/Barang[].Spesifikasi": "kolom",
    "List Item/Barang[].volume": "kolom",
    "List Item/Barang[].unit": "kolom",
    "List Item/Barang[].Periode/Durasi": "kolom",
    "List Item/Barang[].Harga Satuan": "kolom",
    "List Item/Barang[].Jumlah Harga": "kolom",
    "List Item/Barang[].Jenis Biaya": "kolom",  # 2026.10.4; kolom saja (SKIP_KEYS)
    "List Item/Barang[].Keterangan": "kolom",
    # OTC/MRC dsb. Dilewati locator (SKIP_KEYS) karena bentuknya dict bebas.
    "List Item/Barang[].Atribut Tambahan": "tidak_disimpan",
    "Nomor Kontrak Kerja": "kolom",
    "Nomor Kontrak Internal": "kolom",
    "Daftar Nomor Kontrak[]": "hasil_ekstraksi",
    "Tanggal Negosiasi": "hasil_ekstraksi",
    "Nama Pekerjaan": "kolom",
    "persentase ppn": "kolom",
    "Jangka Waktu": "kolom",
    "Durasi Kerja": "kolom",
    "Nama Bank": "kolom",
    "Lokasi Cabang Bank": "hasil_ekstraksi",
    "Nomor Rekening Bank": "kolom",
    "Nama Rekening Bank": "kolom",
    "Mekanisme Skema Pembayaran": "kolom",
    "Ketentuan Pembayaran[]": "kolom",  # 2026.10.4: contract_payment_term
    "Persentase Sanksi/Penalti": "kolom",
    "Lokasi": "kolom",
    "Tanggal Pembuatan Dokumen": "kolom",
    "sub total": "kolom",
    "Total PPN": "kolom",
    "Total Harga Pekerjaan": "kolom",
    "Jumlah Terbilang": "hasil_ekstraksi",
    "Garansi": "hasil_ekstraksi",
    "Klausul Jaminan.Nomor Pasal": "hasil_ekstraksi",
    "Klausul Jaminan.Uraian Jaminan": "hasil_ekstraksi",
    "Syarat Lampiran Wajib BAST[]": "kolom",
    "Dokumen Pendukung[].Nama Dokumen": "hasil_ekstraksi",
    "Dokumen Pendukung[].Nomor Dokumen": "hasil_ekstraksi",
    "Dokumen Pendukung[].Tanggal Dokumen": "hasil_ekstraksi",
    "Daftar Pasal Kontrak[].Nomor Pasal": "hasil_ekstraksi",
    "Daftar Pasal Kontrak[].Judul Pasal": "hasil_ekstraksi",
    "Daftar Penandatangan[].Nama": "hasil_ekstraksi",
    "Daftar Penandatangan[].Jabatan": "hasil_ekstraksi",
    "Informasi Bea Meterai": "hasil_ekstraksi",
    "Daftar Tabel Terstruktur[]": "tidak_disimpan",  # SKIP_KEYS
}


def _jalur_daun(model, awalan=""):
    """Semua field daun model Pydantic sebagai jalur alias; daftar ditulis `nama[]`."""
    import types
    import typing

    from pydantic import BaseModel

    def buka(t):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        if typing.get_origin(t) is list:
            return True, args[0]
        if typing.get_origin(t) in (typing.Union, types.UnionType) and len(args) == 1:
            return buka(args[0])
        return False, t

    for nama, f in model.model_fields.items():
        jalur = awalan + (f.alias or nama)
        daftar, t = buka(f.annotation)
        model_anak = isinstance(t, type) and issubclass(t, BaseModel)
        if daftar:
            yield from _jalur_daun(t, jalur + "[].") if model_anak else [jalur + "[]"]
        elif model_anak:
            yield from _jalur_daun(t, jalur + ".")
        else:
            yield jalur


def _isi(data, jalur, nilai):
    """Isi `nilai` di `jalur` (format _jalur_daun); elemen daftar selalu indeks 0."""
    bagian = jalur.split(".")
    cur = data
    for i, b in enumerate(bagian):
        akhir = i == len(bagian) - 1
        kunci = b.removesuffix("[]")
        if b.endswith("[]"):
            daftar = cur.setdefault(kunci, [])
            if akhir:
                daftar.append(nilai)
                return
            if not daftar:
                daftar.append({})
            cur = daftar[0]
        elif akhir:
            cur[kunci] = nilai
        else:
            cur = cur.setdefault(kunci, {})


# Field berpilihan: pemeta sengaja mengosongkan nilai asing, jadi butuh contoh yang sah.
_NILAI_CONTOH = {"Jenis Biaya": "MRC"}


def nasib_sebenarnya(model, pemeta, dasar, doc_type):
    """
    Uji perilaku, bukan membaca kode pemeta: tiap field diisi sendirian di atas data `dasar`
    (yang cukup supaya baris anak tidak dibuang), lalu dilihat sampai ke mana nilainya.
    Bukti dibangun dengan iter_leaf_fields yang sama dengan engine.
    """
    from app.evidence.locator import iter_leaf_fields

    def jalankan(data):
        bukti = [
            {"field": f, "value": v, "status": "AUTO_VERIFIED"} for f, v in iter_leaf_fields(data)
        ]
        hasil = {"document_type": doc_type, "data": data, "evidence": bukti}
        hasil["run_info"] = {"document_id": "sha-uji"}
        return pemeta(hasil)

    def domain(payload):
        lewati = ("document", "extracted_field", "extraction_run")
        return {k: v for k, v in payload.items() if k not in lewati}

    acuan = domain(jalankan(copy.deepcopy(dasar)))
    nasib = {}
    for jalur in _jalur_daun(model):
        data = copy.deepcopy(dasar)
        _isi(data, jalur, _NILAI_CONTOH.get(jalur.rsplit(".", 1)[-1], "1234567"))
        payload = jalankan(data)
        konkret = jalur.replace("[]", "[0]")
        if domain(payload) != acuan:
            nasib[jalur] = "kolom"
        elif any(r["field_path"] == konkret for r in payload["extracted_field"]):
            nasib[jalur] = "hasil_ekstraksi"
        else:
            nasib[jalur] = "tidak_disimpan"
    return nasib


def _bandingkan_nasib(sebenarnya, tercatat):
    baru = sorted(set(sebenarnya) - set(tercatat))
    hilang = sorted(set(tercatat) - set(sebenarnya))
    assert not (baru or hilang), (
        f"Alias field ekstraksi berubah.\n  baru: {baru}\n  hilang: {hilang}\n"
        "Bila alias diganti nama, keputusan PM (field_review) dengan field_path lama tidak "
        "lagi cocok. Catat nasib field baru di daftar NASIB_*."
    )
    beda = {j: (tercatat[j], sebenarnya[j]) for j in tercatat if tercatat[j] != sebenarnya[j]}
    assert not beda, f"Nasib field berbeda dari catatan (tercatat, sebenarnya): {beda}"


def test_setiap_field_kontrak_punya_nasib_tercatat():
    from app.schemas.contract import ContractExtractionSchema

    dasar = {
        "Pihak Pertama": {"Nama Perusahaan": "PT A"},
        "Pihak Kedua": {"Nama Perusahaan": "PT B"},
        "List Item/Barang": [{"Deskripsi Item/Barang/Pekerjaan": "Barang dasar"}],
    }
    sebenarnya = nasib_sebenarnya(ContractExtractionSchema, map_contract, dasar, "contract")
    _bandingkan_nasib(sebenarnya, NASIB_KONTRAK)


# --------------------------------------------------------------------- termin & OTC/MRC
def _dengan_ketentuan(*baris):
    raw = _sample_contract_extraction()
    raw["data"]["Ketentuan Pembayaran"] = list(baris)
    raw["evidence"].append(
        {
            "field": "Ketentuan Pembayaran[0]",
            "value": baris[0],
            "page": 4,
            "evidence_text": baris[0],
            "status": "AUTO_VERIFIED",
        }
    )
    return map_contract(raw)["contract_payment_term"]


def test_termin_tertulis_tersimpan_dengan_nominalnya():
    rows = _dengan_ketentuan(
        "Termin I (Januari s.d. Maret) sebesar Rp. 27,500,000",
        "Termin II pelunasan sebesar Rp...36.000.000, dibayarkan setelah BAST",
    )
    assert [(r["line_no"], r["term_label_text"], r["amount"]) for r in rows] == [
        (1, "Termin I", 27_500_000),
        (2, "Termin II", 36_000_000),
    ]
    assert rows[0]["evidence_page"] == 4 and rows[0]["evidence_quote"]


def test_kalimat_banyak_termin_tidak_dipecah_atau_dihitung():
    """
    "2 termin, masing-masing Rp X": membuat dua baris atau menulis X sebagai nilai satu
    termin adalah hasil hitungan, bukan yang tertulis (aturan 3). Kalimatnya disimpan utuh.
    """
    rows = _dengan_ketentuan(
        "Pembayaran secara Termin sebanyak 2 (dua) termin, dengan masing-masing termin "
        "sebesar Rp. 100.000.000,-"
    )
    assert len(rows) == 1
    assert rows[0]["term_label_text"] is None and rows[0]["amount"] is None
    assert "masing-masing" in rows[0]["term_text"]


def test_syarat_pembayaran_umum_tersimpan_tanpa_label_termin():
    rows = _dengan_ketentuan(
        "Surat permohonan pembayaran dilampiri kuitansi bermaterai cukup",
        "Uang muka 30% dibayarkan setelah PO terbit",
    )
    assert rows[0]["term_label_text"] is None and rows[0]["amount"] is None
    assert rows[1]["term_label_text"] == "Uang muka"
    assert rows[1]["percentage_text"] == "30%" and rows[1]["amount"] is None


def test_termin_dengan_dua_nominal_tidak_memilih_salah_satu():
    rows = _dengan_ketentuan("Termin I sebesar Rp 10.000.000 dari total Rp 40.000.000")
    assert rows[0]["term_label_text"] == "Termin I" and rows[0]["amount"] is None


def test_jenis_biaya_item_dipetakan_ke_pilihan_skema():
    raw = _sample_contract_extraction()
    items = raw["data"]["List Item/Barang"]
    items[0]["Jenis Biaya"] = "MRC"
    if len(items) > 1:
        items[1]["Jenis Biaya"] = "sewa bulanan"  # nilai asing -> kosong, bukan tebakan
    payload = map_contract(raw)
    jenis = [r["charge_type"] for r in payload["contract_item"]]
    assert jenis[0] == "mrc"
    assert all(j is None for j in jenis[1:])
    assert validate_payload(payload) == []


def test_jenis_biaya_tidak_menambah_antrean_pm():
    """
    Jenis Biaya diturunkan dari judul kolom tabel. Grounding hanya bisa mencari teks "MRC" di
    dokumen, yang tidak membuktikan apa pun, sehingga setiap item muncul sebagai "perlu dicek"
    (kontrak layanan nyata: 5 baris tambahan). Nilainya tetap terlihat PM di Rincian Kontrak.
    """
    from app.evidence.locator import iter_leaf_fields

    data = {"List Item/Barang": [{"Deskripsi Item/Barang/Pekerjaan": "x", "Jenis Biaya": "MRC"}]}
    assert not any(p.endswith("Jenis Biaya") for p, _ in iter_leaf_fields(data))
