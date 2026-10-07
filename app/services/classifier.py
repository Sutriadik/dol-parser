"""
Open ADE — Document classifier (Plan §10.1).

Rule berbasis skor dijalankan lebih dulu; LLM hanya dipanggil bila rule ambigu.
Versi lama memakai urutan if pertama-cocok dengan substring: SPH yang menyebut "SPK"
(atau kata yang memuat "SPK") langsung terklasifikasi kontrak.
"""

import re

from pydantic import BaseModel, Field

from app.config import config

CLASSIFY_WINDOW_CHARS = 3000
TITLE_WINDOW_CHARS = 600
REST_WEIGHT = 0.5  # bobot kata kunci di luar halaman awal (berkas gabungan)

_RULES: dict[str, list[tuple[str, float]]] = {
    "contract": [
        # Mencakup varian dokumen kontrak pengadaan Indonesia: SPK, Kontrak/PKS, dan
        # Nota/Surat Pesanan (dokumen pemesanan ringkas dari pelanggan ke vendor).
        #
        # "KONTRAK" tanpa kualifikasi TIDAK dijadikan sinyal kuat: BAST/dokumen lain sering
        # menyebutnya hanya sebagai rujukan ("Nomor PO / Kontrak", "sesuai dokumen PO/Kontrak")
        # tanpa dokumen itu sendiri berupa kontrak. Begitu juga "PIHAK PERTAMA/KEDUA" -- BAST
        # memakai istilah yang sama persis untuk pihak penyerah/penerima, jadi tidak
        # membedakan kontrak vs BAST dan sengaja TIDAK dipakai sebagai sinyal di sini.
        # SPMK (Surat Perintah Mulai Kerja) = perintah resmi mulai bekerja setelah kontrak
        # ditandatangani -- dokumen keluarga SPK, dialurkan ke schema kontrak yang sama.
        (r"\bSURAT\s+PERINTAH\s+(?:MULAI\s+)?KERJA\b", 3.0),
        (r"\bSPM?K\b", 2.0),
        (r"\bPERJANJIAN\s+KERJA\s*SAMA\b", 3.0),
        (r"\bPERJANJIAN\b", 2.0),
        (r"\bPKS\b", 2.0),
        (r"\bKONTRAK\s+LAYANAN\b", 2.5),
        (r"\bKONTRAK\s+PENGADAAN\b", 2.5),
        (r"\bKONTRAK\b", 0.75),
        (r"\bNOTA\s+PESANAN\b", 3.0),
        (r"\bSURAT\s+PESANAN\b", 3.0),
        (r"\bPURCHASE\s+ORDER\b", 1.5),
        # Berita Acara Klarifikasi/Negosiasi (BAK) adalah dokumen penetapan harga hasil
        # negosiasi; di lapangan ia jadi halaman muka berkas kontrak (Nota Pesanan/SPK ada di
        # halaman berikutnya), jadi dialurkan ke schema kontrak -- bukan BAST (serah terima)
        # dan bukan SPH (penawaran sepihak dari vendor).
        (r"\bBERITA\s+ACARA\s+KLARIFIKASI\b", 3.0),
        (r"\bBERITA\s+ACARA\s+NEGO(?:SIASI)?\b", 3.0),
        (r"\bKLARIFIKASI\s+DAN\s+NEGOSIASI\b", 3.0),
        (r"\bHASIL\s+NEGOSIASI\b", 1.5),
    ],
    "sph": [
        (r"\bSURAT\s+PENAWARAN\s+HARGA\b", 3.0),
        (r"\bSPH\b", 2.0),
        (r"\bPENAWARAN\s+HARGA\b", 2.5),
        (r"\bPRICE\s+QUOTATION\b", 3.0),
        (r"\bQUOTATION\b", 2.0),
        (r"\bPENAWARAN\s+BIAYA\b", 2.5),
    ],
    "bast": [
        (r"\bBERITA\s+ACARA\s+SERAH\s+TERIMA\b", 3.5),
        (r"\bBAST\b", 2.5),
        (r"\bSERAH\s+TERIMA\s+PEKERJAAN\b", 2.5),
        (r"\bBERITA\s+ACARA\s+PENYELESAIAN\b", 2.5),
        (r"\bBERITA\s+ACARA\s+PENERIMAAN\s+PEKERJAAN\b", 3.0),
        (r"\bBERITA\s+ACARA\s+UJI\s+TERIMA\b", 2.0),
    ],
}
MIN_SCORE = 2.0


class DocumentClassificationResult(BaseModel):
    document_type: str = Field(
        description="Jenis dokumen: 'contract', 'sph', 'bast', atau 'general'"
    )
    confidence: float = Field(description="Skor keyakinan (0.0 - 1.0)")
    reasoning: str = Field(description="Alasan penentuan tipe dokumen")
    title: str | None = Field(None, description="Judul dokumen yang terdeteksi")


class DocumentClassifier:
    def __init__(self, model_name: str = None, base_url: str = None):
        self.model_name = model_name or config.OLLAMA_MODEL
        self.base_url = base_url or config.OLLAMA_BASE_URL
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self.base_url)
        return self._client

    @staticmethod
    def score_types(text: str) -> dict[str, float]:
        """
        Tiga lapis bobot: judul/kop > halaman awal > sisa dokumen.

        Lapis ketiga penting untuk berkas gabungan: dokumen kontrak sering dikirim sebagai
        satu PDF dengan Berita Acara Negosiasi di depan dan Nota Pesanan/SPK di halaman
        tengah. Tanpa lapis ini, tipe seluruh berkas ditentukan oleh 3.000 karakter pertama
        saja -- satu kemunculan "Penawaran Harga" di posisi 2.983 pernah membuat berkas
        kontrak 9 halaman terklasifikasi sebagai SPH.
        """
        upper = text.upper()
        window = upper[:CLASSIFY_WINDOW_CHARS]
        title = window[:TITLE_WINDOW_CHARS]
        rest = upper[CLASSIFY_WINDOW_CHARS:]
        scores: dict[str, float] = {}
        for doc_type, patterns in _RULES.items():
            score = 0.0
            for pattern, weight in patterns:
                if re.search(pattern, title):
                    score += weight * 1.5  # kata kunci di judul/kop jauh lebih bermakna
                elif re.search(pattern, window):
                    score += weight
                elif re.search(pattern, rest):
                    score += weight * REST_WEIGHT
            scores[doc_type] = score
        return scores

    def classify_fast_rule(self, text: str) -> tuple[str, float]:
        scores = self.score_types(text)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        (best_type, best), (_, second) = ranked[0], ranked[1]
        if best >= MIN_SCORE and best >= second * 1.5:
            return best_type, 0.98
        if best >= MIN_SCORE:
            return best_type, 0.80
        return "contract", 0.70

    def classify(self, markdown_text: str) -> DocumentClassificationResult:
        rule_type, rule_conf = self.classify_fast_rule(markdown_text)
        title = re.sub(r"[#*]", "", markdown_text[:100]).strip()
        if rule_conf > 0.90:
            return DocumentClassificationResult(
                document_type=rule_type,
                confidence=rule_conf,
                reasoning=f"Rule score: {self.score_types(markdown_text)}",
                title=title,
            )

        system_prompt = (
            "Anda adalah AI Classifier dokumen pengadaan, hukum, dan administrasi.\n"
            "Tentukan jenis dokumen berikut:\n"
            "- 'contract': Surat Perintah Kerja (SPK), Perjanjian/Kontrak Kerja Sama (PKS), atau "
            "Nota/Surat Pesanan.\n"
            "- 'sph': Surat Penawaran Harga dari vendor/penyedia.\n"
            "- 'bast': Berita Acara Serah Terima pekerjaan/barang.\n"
            "- 'general': dokumen lain."
        )
        try:
            response = self.client.chat(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Cuplikan halaman awal dokumen:\n\n{markdown_text[:2000]}"
                            "\n\nTentukan tipe dokumen dalam JSON."
                        ),
                    },
                ],
                format=DocumentClassificationResult.model_json_schema(),
                # num_ctx WAJIB sama dengan yang dipakai extractor. Nilai berbeda memaksa
                # Ollama mengalokasi ulang konteks model, yang membuang KV-cache dan
                # memperlambat panggilan ekstraksi berikutnya.
                options={
                    "temperature": 0.0,
                    "seed": config.OLLAMA_SEED,
                    "num_ctx": config.OLLAMA_NUM_CTX,
                },
                keep_alive=config.OLLAMA_KEEP_ALIVE,
            )
            return DocumentClassificationResult.model_validate_json(response["message"]["content"])
        except Exception:
            return DocumentClassificationResult(
                document_type=rule_type,
                confidence=rule_conf,
                reasoning="Rule-based classification fallback",
                title=title,
            )
