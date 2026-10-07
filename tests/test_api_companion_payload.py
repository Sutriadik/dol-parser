"""
Jalur API (`/process-all`, `/jobs`) harus menghasilkan payload dengan tabel dol_schema.

Sebelumnya API memakai exporter lama dengan nama tabel sendiri (`contracts`, `sph_offers`),
sehingga push ke NocoDB menulis nol baris. Tes ini menjaga supaya API dan CLI companion
memakai satu skema yang sama.
"""

import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import dol_schema
from app import main
from app.companion import SkemaTidakCocok, cek_versi_skema
from app.config import config
from app.services.job_queue import Job, JobQueue
from dol_schema import ALL_TABLES, validate_payload
from tests.test_companion_contract import _sample_contract_extraction


def _dump(value):
    return SimpleNamespace(model_dump=lambda **_: value)


def _engine_result(extraction):
    """Bentuk keluaran OpenADEEngine.process_full, cukup untuk _build_api_response."""
    return {
        "document_type": extraction["document_type"],
        "extracted": _dump(extraction["data"]),
        "validation": _dump({"status": "PASS", "issues": []}),
        "evidence": [
            _dump(
                {
                    "field": "Nomor Kontrak Kerja",
                    "value": "123/00/XYZ-01/BUT/2026",
                    "page": 1,
                    "evidence_text": "Nomor: 123/00/XYZ-01/BUT/2026",
                    "evidence_score": 0.97,
                    "status": "AUTO_VERIFIED",
                }
            )
        ],
        "quality_report": {"fill_rate": 0.9},
        "run_info": {"document_id": "doc-uji", "page_count": 2, "timings": {}},
        "parsed": SimpleNamespace(markdown="# SPK", metadata=_dump({})),
        "files": {"extract_json": "storage/outputs/SPK-uji.extract.json"},
    }


def test_companion_payload_memakai_tabel_dol_schema(tmp_path):
    pdf = tmp_path / "SPK-uji.pdf"
    pdf.write_bytes(b"%PDF-1.4 uji")
    resp = main._build_api_response(_engine_result(_sample_contract_extraction()), pdf)

    payload = resp["companion_payload"]
    skema = {t.name for t in ALL_TABLES}
    assert set(payload) <= skema
    assert payload["contract"][0]["contract_number"] == "123/00/XYZ-01/BUT/2026"
    assert validate_payload(payload) == []
    # Kunci dokumen dari byte berkas unggahan -- sama dengan document_id yang dibalas ke n8n.
    from app.document_ir.adapter import content_hash

    assert payload["document"][0]["content_hash"] == content_hash(pdf)
    assert resp["companion_catatan"] is None
    assert "nocodb_payload" not in resp


def test_jenis_dokumen_tanpa_pemeta_menghasilkan_none():
    extraction = dict(_sample_contract_extraction(), document_type="unknown")
    resp = main._build_api_response(_engine_result(extraction))
    assert resp["companion_payload"] is None
    assert "belum punya pemeta" in resp["companion_catatan"]


def test_bast_tidak_dikirim_karena_tabelnya_ditunda():
    extraction = dict(_sample_contract_extraction(), document_type="bast")
    resp = main._build_api_response(_engine_result(extraction))
    assert resp["companion_payload"] is None
    assert "ditunda" in resp["companion_catatan"]


def test_push_tanpa_payload_companion_ditolak(monkeypatch):
    monkeypatch.setattr(config, "NOCODB_PUSH_ENABLED", True)
    with pytest.raises(HTTPException) as e:
        main._push_to_nocodb(None)
    assert e.value.status_code == 422


# ---------------------------------------------------------------- push dari jalur /jobs
# n8n memakai /jobs (asinkron). Push ke NocoDB harus bisa diminta dari sana juga, dan push
# yang gagal tidak boleh membuang hasil ekstraksi.
def _job_uji(tmp_path, push):
    pdf = tmp_path / "SPK-uji.pdf"
    pdf.write_bytes(b"%PDF-1.4 uji")
    return Job(
        job_id="job-uji",
        document_id="doc-uji",
        filename=pdf.name,
        doc_type="auto",
        ocr=None,
        max_pages=None,
        callback_url=None,
        source_path=pdf,
        push_to_nocodb=push,
    )


@pytest.fixture
def engine_boneka(monkeypatch):
    hasil = _engine_result(_sample_contract_extraction())
    monkeypatch.setattr(main, "engine", SimpleNamespace(process_full=lambda *a, **k: hasil))


def test_job_dengan_push_menulis_ke_nocodb(tmp_path, monkeypatch, engine_boneka):
    terkirim = []
    monkeypatch.setattr(
        main, "_push_to_nocodb", lambda p: terkirim.append(p) or {"contract": {"insert": 1}}
    )
    job = _job_uji(tmp_path, push=True)
    resp = main._run_job(job)

    assert len(terkirim) == 1 and terkirim[0] is resp["companion_payload"]
    assert resp["nocodb_push"] == {"contract": {"insert": 1}}
    assert job.nocodb_push == "ok"
    assert job.summary()["nocodb_push"] == "ok"


def test_push_gagal_tidak_membuang_hasil_ekstraksi(tmp_path, monkeypatch, engine_boneka):
    def gagal(_):
        raise HTTPException(status_code=502, detail="NocoDB tidak menjawab")

    monkeypatch.setattr(main, "_push_to_nocodb", gagal)
    job = _job_uji(tmp_path, push=True)
    resp = main._run_job(job)

    assert resp["companion_payload"]["contract"]
    assert resp["nocodb_push"] == {"error": "NocoDB tidak menjawab"}
    assert job.nocodb_push == "gagal: NocoDB tidak menjawab"


def test_job_tanpa_push_tidak_menyentuh_nocodb(tmp_path, monkeypatch, engine_boneka):
    monkeypatch.setattr(main, "_push_to_nocodb", lambda p: pytest.fail("push tidak diminta"))
    job = _job_uji(tmp_path, push=False)
    resp = main._run_job(job)

    assert resp["nocodb_push"] is None
    assert job.nocodb_push is None


def test_push_diminta_saat_dimatikan_ditolak_sebelum_antri(monkeypatch):
    monkeypatch.setattr(config, "NOCODB_PUSH_ENABLED", False)
    antrian = JobQueue(runner=lambda job: pytest.fail("job tidak boleh dijalankan"))
    monkeypatch.setattr(main, "job_queue", antrian)
    with TestClient(main.app) as c:
        r = c.post(
            "/api/v1/jobs",
            files={"file": ("SPH.pdf", io.BytesIO(b"%PDF-1.4 uji"), "application/pdf")},
            data={"push_to_nocodb": "true"},
        )
    assert r.status_code == 409
    assert antrian.list_jobs() == []


# ---------------------------------------------------------------- versi dol-schema
# dol-schema dipasang dari folder sejajar tanpa versi terkunci; kode ini harus menolak jalan
# dengan pesan jelas bila versinya bukan yang dipahami pemeta.
def test_versi_terpasang_sama_dengan_yang_diharapkan():
    assert dol_schema.SCHEMA_VERSION == config.DOL_SCHEMA_VERSION


def test_versi_berbeda_ditolak_dengan_petunjuk():
    with pytest.raises(SkemaTidakCocok) as e:
        cek_versi_skema("companion-2026.09.1", "companion-2026.10.2")
    pesan = str(e.value)
    assert "companion-2026.09.1" in pesan and "git pull" in pesan and "DOL_SCHEMA_VERSION" in pesan
