"""
Tests for FastAPI endpoints in Open ADE (app/main.py).
"""

import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "Docling" in data["engine"]
    assert "docs_url" in data


def test_health_check_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["healthy", "degraded", "unhealthy"]
    assert "storage" in data
    assert "ollama" in data
    assert "version" in data


def test_api_v1_health_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert "storage" in data
    assert "ollama" in data


def test_api_v1_info_endpoint():
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert ".pdf" in data["allowed_file_types"]
    assert "CONTRACT" in data["supported_document_types"]
    assert "ollama_config" in data
    assert "parser_config" in data


def test_parse_invalid_file_extension():
    # Attempt to upload an unsupported file extension (.exe)
    fake_file = io.BytesIO(b"dummy binary content")
    response = client.post(
        "/api/v1/parse",
        files={"file": ("malicious.exe", fake_file, "application/octet-stream")},
        data={"parser": "auto"},
    )
    assert response.status_code == 400
    assert "Format tidak didukung" in response.json()["detail"]


def test_process_all_invalid_file_extension():
    fake_file = io.BytesIO(b"dummy binary content")
    response = client.post(
        "/api/v1/process-all",
        files={"file": ("test.txt", fake_file, "text/plain")},
        data={"doc_type": "auto"},
    )
    assert response.status_code == 400
    assert "Format tidak didukung" in response.json()["detail"]
