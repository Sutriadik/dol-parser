"""
Saat Ollama mati, pengguna harus melihat pesan yang menyebut cara memperbaikinya.

Pustaka `ollama` tidak meneruskan httpx.ConnectError: ia menggantinya dengan ConnectionError
bawaan berbunyi "Failed to connect to Ollama...". Kode lama hanya menangkap
httpx.ConnectError, jadi pesan Indonesia di `_chat` tidak pernah tampil (terjadi nyata
29 Sep 2026 saat Ollama tertutup semalaman).
"""

import httpx
import pytest

from app.extractors.ollama_client import OllamaExtractor


class _ClientMati:
    def __init__(self, exc):
        self.exc = exc

    def chat(self, **_):
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError(
            "Failed to connect to Ollama. Please check that Ollama is downloaded, "
            "running and accessible. https://ollama.com/download"
        ),
        httpx.ConnectError("[Errno 61] Connection refused"),
    ],
)
def test_ollama_mati_memberi_pesan_yang_bisa_ditindaklanjuti(exc):
    extractor = OllamaExtractor()
    extractor.client = _ClientMati(exc)
    with pytest.raises(ConnectionError, match="Buka aplikasi Ollama"):
        extractor._chat([{"role": "user", "content": "uji"}])
