import os
from pathlib import Path

from pydantic import BaseModel


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class AppConfig(BaseModel):
    # Base paths
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    STORAGE_DIR: Path = BASE_DIR / "storage"
    TEMP_UPLOADS: Path = STORAGE_DIR / "temp_uploads"
    OUTPUT_DIR: Path = STORAGE_DIR / "outputs"
    PARSING_OUTPUT_DIR: Path = OUTPUT_DIR / "parsing"
    # .extract.json — hasil ekstraksi lengkap: nilai field + evidence + bounding box.
    EXTRACTION_OUTPUT_DIR: Path = OUTPUT_DIR / "extraction"

    # Ollama settings
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    OLLAMA_TEMPERATURE: float = 0.0
    OLLAMA_SEED: int = _env_int("OLLAMA_SEED", 42)
    # Wajib eksplisit: default server Ollama (umumnya 4096) memotong prompt kontrak (~8k token)
    # diam-diam.
    OLLAMA_NUM_CTX: int = _env_int("OLLAMA_NUM_CTX", 16384)
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "600"))
    # Batas token output: mencegah model "berputar" menghasilkan JSON tanpa akhir sampai timeout.
    OLLAMA_NUM_PREDICT: int = _env_int("OLLAMA_NUM_PREDICT", 4096)
    ENABLE_LLM_MARKDOWN_REFINER: bool = _env_bool("ENABLE_LLM_MARKDOWN_REFINER", True)

    # Parser settings
    # docling (Apple Vision OCR di macOS) ~17 detik untuk 9 halaman scan; jalur paddle
    # merender DAN meng-OCR tiap halaman dua kali (PaddleOCR lalu PP-Structure), jadi
    # dipakai hanya sebagai fallback.
    DEFAULT_PARSER: str = os.getenv("DEFAULT_PARSER", "docling")
    PARSER_NUM_THREADS: int = _env_int("PARSER_NUM_THREADS", max(1, (os.cpu_count() or 4) - 2))
    # SATU tombol pemilihan mesin OCR: rapidocr | mac | tesseract | paddle | auto
    # Bisa juga dioper sebagai argumen Python: engine.parse(pdf, ocr="tesseract").
    #
    # Default rapidocr (BUKAN mac/auto) supaya hasil di laptop pengembang sama dengan hasil
    # di server produksi Linux/Windows. Apple Vision lebih cepat & sedikit lebih akurat, tapi
    # hanya ada di macOS -- kalau dijadikan default, setiap angka akurasi yang kita ukur di
    # laptop jadi janji yang tidak bisa ditepati produksi.
    OCR_ENGINE: str = os.getenv("OCR_ENGINE", "rapidocr")
    DEFAULT_DPI: int = 150
    OCR_LANG: str = os.getenv("OCR_LANG", "en")
    SCANNED_CHAR_THRESHOLD: int = 40  # rata-rata karakter/halaman di bawah ini = scan
    PAGE_NATIVE_MIN_CHARS: int = 80  # halaman dengan teks native >= ini tidak perlu OCR

    # OCR Spatial Clustering settings
    OCR_Y_TOLERANCE: float = 0.015
    OCR_X_GAP_TOLERANCE: float = 0.15

    # Extraction settings
    MAX_EXTRACTION_RETRIES: int = _env_int("MAX_EXTRACTION_RETRIES", 1)
    NULL_FIELD_THRESHOLD: float = 0.30  # Retry jika > 30% fields null
    # Konteks efektif dinaikkan ke 32.000 karakter agar seluruh isi dokumen (hingga ~15-20 halaman)
    # terbaca utuh oleh Qwen 2.5 (12K context window) untuk menjaga rich context & visual grounding.
    EFFECTIVE_TEXT_MAX_CHARS: int = _env_int("EFFECTIVE_TEXT_MAX_CHARS", 32000)

    # Grounding Linker settings
    GROUNDING_MIN_SCORE: float = (
        0.65  # Minimum calibrated score untuk visual grounding bounding box
    )

    # PP-Structure Layout Analysis settings
    ENABLE_LAYOUT_ANALYSIS: bool = True  # Gunakan PPStructure untuk scanned docs (fallback engine)

    # --- Keamanan API (dipanggil n8n) ------------------------------------------------
    # development | production. Di production, kelonggaran yang memudahkan pengembangan di
    # laptop dimatikan: API key wajib (server menolak naik tanpanya), callback hanya ke host
    # di CALLBACK_ALLOWED_HOSTS, dan galat 500 hanya membalas nomor rujukan -- pesan
    # exception bisa memuat potongan isi kontrak atau path server.
    ENV: str = os.getenv("OPENADE_ENV", "development").strip().lower()
    # Bila diisi, setiap endpoint /api/v1/* selain health wajib mengirim header
    # `X-API-Key`. Sengaja BUKAN default acak: server yang diam-diam menolak semua
    # permintaan lebih sulit didiagnosis daripada server yang terbuka lalu ditutup sadar.
    # WAJIB diisi sebelum service ini bisa dijangkau dari luar localhost.
    API_KEY: str = os.getenv("OPENADE_API_KEY", "")
    # Daftar origin browser yang boleh memanggil, dipisah koma. Default hanya localhost:
    # versi sebelumnya memakai "*" bersama allow_credentials=True -- kombinasi yang ditolak
    # spesifikasi CORS dan membuka API ke situs mana pun bila sempat diterima.
    # n8n memanggil dari server (tanpa Origin), jadi tidak terpengaruh nilai ini.
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
    # Ukuran unggahan maksimum. PDF kontrak/BAST di sample_pdfs terbesar ~10 MB.
    MAX_UPLOAD_MB: int = _env_int("MAX_UPLOAD_MB", 50)
    # Timeout saat service ini mem-POST hasil ke callback_url milik n8n.
    CALLBACK_TIMEOUT: float = float(os.getenv("CALLBACK_TIMEOUT", "30"))
    # Host yang boleh menerima callback, dipisah koma (mis. "n8n.internal,localhost"). Tanpa
    # daftar ini, pemegang API key bisa menyuruh server mem-POST hasil ekstraksi ke alamat
    # mana saja, termasuk layanan internal (SSRF). Kosong = bebas di development, ditolak
    # di production.
    CALLBACK_ALLOWED_HOSTS: str = os.getenv("CALLBACK_ALLOWED_HOSTS", "")
    # Bila diisi, setiap callback membawa header `X-OpenADE-Signature: sha256=<hmac>` atas
    # body mentahnya, supaya n8n bisa menolak kiriman yang bukan dari service ini.
    CALLBACK_SECRET: str = os.getenv("CALLBACK_SECRET", "")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # NocoDB settings (integrasi opsional; push mati secara default)
    NOCODB_URL: str = os.getenv("NOCODB_URL", "http://localhost:8080")
    NOCODB_API_TOKEN: str = os.getenv("NOCODB_API_TOKEN", "")
    # NocoDB v2 memakai tableId acak (mis. "m1a2b3c4d5"), bukan nama tabel, jadi pemetaan
    # harus diberikan eksplisit -- tidak bisa ditebak dari nama. Kunci = nama teknis tabel
    # dol_schema. Dicetak oleh `scripts/nocodb_setup.py --create-base`. Format env:
    #   NOCODB_TABLE_IDS="document:m1abc,contract:m2def,extracted_field:m3ghi"
    NOCODB_TABLE_IDS: str = os.getenv("NOCODB_TABLE_IDS", "")
    # Push langsung ke NocoDB dari FastAPI. Default mati: pada arsitektur briefing (hlm. 8)
    # n8n yang mengorkestrasi, FastAPI cukup mengembalikan payload-nya.
    NOCODB_PUSH_ENABLED: bool = _env_bool("NOCODB_PUSH_ENABLED", False)

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    def callback_hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.CALLBACK_ALLOWED_HOSTS.split(",") if h.strip()}

    def cors_origins(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    def nocodb_table_ids(self) -> dict:
        """Parse NOCODB_TABLE_IDS jadi {nama_tabel: tableId}."""
        mapping = {}
        for pair in self.NOCODB_TABLE_IDS.split(","):
            if ":" in pair:
                name, _, table_id = pair.partition(":")
                name, table_id = name.strip(), table_id.strip()
                if name and table_id:
                    mapping[name] = table_id
        return mapping

    # Perusahaan kita. Dipakai untuk menentukan peran pihak kontrak (BUT = pelaksana) dan arah
    # BAST. Label "Pihak Pertama/Kedua" tidak bisa dipakai: posisinya terbalik antar-format.
    OWN_COMPANY_PATTERNS: tuple = (r"bhakti\s*unggul\s*teknovasi", r"\bBUT\b")

    # Versioning (Plan §27) — naikkan saat parser/schema berubah.
    # SCHEMA_VERSION = versi skema EKSTRAKSI (Pydantic di app/schemas/), bukan skema database.
    PARSER_VERSION: str = "parse-2026.09.1"
    SCHEMA_VERSION: str = "schema-2026.09.1"

    # Versi skema DATABASE (repo dol-schema) yang dipahami kode ini. dol-schema dipasang dari
    # folder sejajar, jadi yang terpasang adalah apa pun yang sedang ada di folder itu --
    # app/companion menolak jalan bila versinya berbeda. Naikkan bersama perubahan pemeta.
    DOL_SCHEMA_VERSION: str = "companion-2026.10.4"


config = AppConfig()

# Ensure directories exist
config.TEMP_UPLOADS.mkdir(parents=True, exist_ok=True)
config.PARSING_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
config.EXTRACTION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
