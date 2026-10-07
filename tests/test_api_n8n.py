"""
Tes endpoint yang dipanggil n8n: autentikasi, batas unggahan, dan siklus hidup job.

Engine berat (Docling/Ollama) tidak pernah dipanggil di sini — runner antrian diganti
boneka. Yang diuji adalah kontrak HTTP-nya, karena itu yang dipegang n8n.
"""

import io

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import config
from app.services.job_queue import DONE, JobQueue

PDF = b"%PDF-1.4\n% dokumen uji\n"


@pytest.fixture
def client(monkeypatch):
    """Antrian dengan runner boneka; job selesai seketika tanpa memuat model apa pun."""
    antrian = JobQueue(
        runner=lambda job: {
            "status": "success",
            "run_info": {"document_id": job.document_id},
            "companion_payload": {"document": [{"content_hash": job.document_id}]},
        }
    )
    monkeypatch.setattr(main, "job_queue", antrian)
    with TestClient(main.app) as c:
        antrian.start()
        yield c


def _kirim(client, nama="SPK.pdf", isi=PDF, **form):
    return client.post(
        "/api/v1/jobs",
        files={"file": (nama, io.BytesIO(isi), "application/pdf")},
        data=form or None,
    )


# --------------------------------------------------------------------- autentikasi
def test_tanpa_api_key_terbuka_saat_env_kosong(client, monkeypatch):
    """Default pengembangan: OPENADE_API_KEY kosong = tidak ada autentikasi."""
    monkeypatch.setattr(config, "API_KEY", "")
    assert _kirim(client).status_code == 202


def test_api_key_salah_ditolak_401(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "rahasia")
    r = client.post(
        "/api/v1/jobs",
        files={"file": ("a.pdf", io.BytesIO(PDF), "application/pdf")},
        headers={"X-API-Key": "tebakan"},
    )
    assert r.status_code == 401


def test_api_key_benar_diterima(client, monkeypatch):
    monkeypatch.setattr(config, "API_KEY", "rahasia")
    r = client.post(
        "/api/v1/jobs",
        files={"file": ("a.pdf", io.BytesIO(PDF), "application/pdf")},
        headers={"X-API-Key": "rahasia"},
    )
    assert r.status_code == 202


def test_health_tetap_terbuka_walau_api_key_aktif(client, monkeypatch):
    """Probe health milik Docker/uptime monitor tidak mengirim API key."""
    monkeypatch.setattr(config, "API_KEY", "rahasia")
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


# --------------------------------------------------------------------- validasi unggahan
def test_format_tidak_didukung_ditolak(client):
    r = client.post(
        "/api/v1/jobs", files={"file": ("data.exe", io.BytesIO(b"MZ"), "application/octet-stream")}
    )
    assert r.status_code == 400
    assert "Format tidak didukung" in r.json()["detail"]


def test_berkas_melebihi_batas_ditolak_413(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    r = _kirim(client, isi=b"x" * (2 * 1024 * 1024))
    assert r.status_code == 413
    assert "melebihi batas" in r.json()["detail"]


# --------------------------------------------------------------------- siklus hidup job
def test_submit_membalas_seketika_dengan_document_id(client):
    """
    Inti desainnya: n8n mendapat document_id SEBELUM dokumen diproses, jadi ia bisa
    memakainya sebagai kunci idempotensi tanpa menunggu 3-5 menit.
    """
    r = _kirim(client)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["document_id"].startswith("doc-")
    assert body["poll_url"] == f"/api/v1/jobs/{body['job_id']}"


def test_document_id_sama_untuk_berkas_yang_sama(client):
    """Kirim ulang PDF yang sama -> document_id sama -> NocoDB meng-UPDATE, bukan menggandakan."""
    a = _kirim(client, nama="SPK.pdf").json()
    b = _kirim(client, nama="nama-lain-dari-n8n.pdf").json()
    assert a["document_id"] == b["document_id"]
    assert a["job_id"] != b["job_id"]


def test_polling_mengembalikan_200_walau_belum_selesai(client):
    """
    Status job ada di body, bukan di kode HTTP: kode selain 2xx membuat node n8n
    menganggapnya error dan menghentikan alur.
    """
    job_id = _kirim(client).json()["job_id"]
    r = client.get(f"/api/v1/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["status"] in ("queued", "running", "done")


def test_hasil_lengkap_diambil_dengan_include_result(client):
    job_id = _kirim(client).json()["job_id"]
    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{job_id}", params={"include_result": True}).json()
        if body["status"] == DONE:
            break
    assert body["status"] == DONE
    assert "companion_payload" in body["result"]
    assert body["result"]["companion_payload"]["document"][0]["content_hash"] == body["document_id"]


def test_ringkasan_tidak_memuat_hasil_kecuali_diminta(client):
    """Hasil bisa puluhan MB (markdown + evidence); polling tidak boleh menyeretnya tiap kali."""
    job_id = _kirim(client).json()["job_id"]
    for _ in range(200):
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] == DONE:
            break
    assert body["status"] == DONE
    assert "result" not in body


def test_job_tidak_dikenal_404(client):
    assert client.get("/api/v1/jobs/job-tidakada").status_code == 404


def test_daftar_job_bisa_disaring_per_document_id(client):
    doc_id = _kirim(client).json()["document_id"]
    r = client.get("/api/v1/jobs", params={"document_id": doc_id})
    assert r.status_code == 200
    assert all(j["document_id"] == doc_id for j in r.json()["jobs"])
    assert "stats" in r.json()


# --------------------------------------------------------------------- CORS
def test_cors_tidak_lagi_wildcard():
    """
    `allow_origins=["*"]` bersama `allow_credentials=True` ditolak spesifikasi CORS dan
    membuka API ke situs mana pun. Daftar origin harus eksplisit.
    """
    assert "*" not in config.cors_origins()
    assert config.cors_origins()
