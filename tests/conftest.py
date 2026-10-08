"""Fixture yang berlaku untuk semua tes."""

import pytest

from app.config import config


@pytest.fixture(autouse=True)
def folder_unggahan_terpisah(tmp_path, monkeypatch):
    """
    Tes API mengunggah berkas lewat TestClient. Dulu berkas itu masuk ke storage/temp_uploads
    yang sungguhan dan tidak pernah dihapus (runner antrean di tes adalah boneka yang tidak
    memanggil _cleanup): 1066 berkas menumpuk per 2026-10-07.
    """
    folder = tmp_path / "temp_uploads"
    folder.mkdir()
    monkeypatch.setattr(config, "TEMP_UPLOADS", folder)
    return folder
