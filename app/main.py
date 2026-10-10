import hashlib
import hmac
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.companion.payload import build_companion_payload, payload_note
from app.config import config
from app.document_ir.adapter import content_hash
from app.logger import logger
from app.services.engine import OpenADEEngine
from app.services.job_queue import DONE, FAILED, QUEUED, Job, JobQueue, new_job_id

tags_metadata = [
    {
        "name": "System & Health",
        "description": "Health check, konfigurasi, dan status ketersediaan model/engine.",
    },
    {
        "name": "Document Parsing",
        "description": (
            "Ekstraksi layout, reading order, struktur tabel, dan Markdown (Docling / PaddleOCR)."
        ),
    },
    {
        "name": "Document Extraction",
        "description": "Ekstraksi data terstruktur berbasis skema Pydantic & LLM Qwen 2.5.",
    },
    {
        "name": "End-to-End Pipeline",
        "description": (
            "Pipeline terpadu (Ingest -> Parse -> Classify -> Extract -> Validate -> Evidence "
            "Grounding)."
        ),
    },
    {
        "name": "Integrasi n8n",
        "description": (
            "Jalur ASINKRON yang dipakai n8n. Satu dokumen butuh 160-310 detik, "
            "jauh di atas timeout HTTP n8n/proxy, jadi pemrosesan dilakukan di antrian: "
            "POST /api/v1/jobs membalas seketika, hasilnya diambil lewat callback atau polling."
        ),
    },
]


def _hapus_unggahan_yatim() -> None:
    """
    Antrean job ada di memori, jadi saat server naik tidak ada job yang memegang berkas di
    TEMP_UPLOADS: semua isinya sisa job yang terputus restart. Tanpa ini, PDF kontrak yang
    diunggah sebelum server mati tertinggal di disk selamanya. Aman karena server berjalan
    satu proses (docker-compose, make serve); dengan --workers > 1 ini harus dipindah.
    """
    yatim = [p for p in config.TEMP_UPLOADS.iterdir() if p.is_dir()]
    for p in yatim:
        shutil.rmtree(p, ignore_errors=True)
    if yatim:
        logger.warning(f"{len(yatim)} unggahan dari job yang terputus restart dihapus.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Worker antrian dijalankan saat server naik, bukan saat modul di-import."""
    if config.is_production and not config.API_KEY:
        # Gagal naik, bukan sekadar peringatan: baris log mudah terlewat, sedangkan server
        # produksi tanpa autentikasi membuka seluruh hasil ekstraksi kontrak.
        raise RuntimeError(
            "OPENADE_ENV=production tetapi OPENADE_API_KEY kosong. Isi API key, atau jalankan "
            "dengan OPENADE_ENV=development bila memang hanya dijangkau dari localhost."
        )
    _hapus_unggahan_yatim()
    job_queue.start()
    if not config.API_KEY:
        logger.warning(
            "OPENADE_API_KEY kosong: API terbuka tanpa autentikasi. "
            "Aman hanya selama service ini cuma dijangkau dari localhost."
        )
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Open ADE - Document Parser & Extractor",
    description="Evidence-grounded Document AI & Extraction untuk Kontrak/SPK, SPH, dan BAST "
    "(Docling, PaddleOCR, Ollama Qwen 2.5)",
    version="1.1.0",
    openapi_tags=tags_metadata,
)

# CORS untuk dashboard/web client. Daftar origin dibatasi lewat env CORS_ORIGINS:
# `allow_origins=["*"]` bersama `allow_credentials=True` adalah kombinasi yang ditolak
# spesifikasi CORS, dan bila diterima browser ia membuka API ini ke situs mana pun.
# n8n memanggil dari sisi server (tanpa header Origin), jadi tidak terpengaruh batas ini.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def require_api_key(x_api_key: str = Header(None, alias="X-API-Key")) -> None:
    """
    Autentikasi sederhana antar-service (n8n -> FastAPI).

    Mati bila OPENADE_API_KEY kosong, supaya pengembangan di localhost tidak terganggu.
    Begitu service ini dijangkau dari luar localhost, isi env tersebut: tanpa itu siapa pun
    yang bisa menjangkau portnya dapat mengunggah dokumen dan membaca seluruh hasil
    ekstraksi kontrak pelanggan.
    """
    if not config.API_KEY:
        return
    if x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="X-API-Key tidak valid atau tidak dikirim.")


def _galat_internal(e: Exception) -> HTTPException:
    """
    Galat tak terduga -> 500. Jejak lengkapnya hanya di log server, dicari lewat nomor
    rujukan. Di production pesan exception tidak dikirim ke klien: isinya bisa memuat
    potongan dokumen kontrak atau path server.
    """
    rujukan = uuid.uuid4().hex[:8]
    logger.exception(f"Galat internal (rujukan {rujukan}): {type(e).__name__}: {e}")
    if config.is_production:
        return HTTPException(status_code=500, detail=f"Kesalahan internal. Rujukan: {rujukan}")
    return HTTPException(status_code=500, detail=f"{type(e).__name__}: {e} (Rujukan: {rujukan})")


def _cek_callback_url(url: str | None) -> None:
    """Ditolak saat job dikirim, bukan setelah 3-5 menit ekstraksi."""
    if not url:
        return
    bagian = urlsplit(url)
    if bagian.scheme not in ("http", "https") or not bagian.hostname:
        raise HTTPException(
            status_code=400, detail="callback_url harus URL http(s) lengkap dengan host."
        )
    izin = config.callback_hosts()
    if not izin:
        if config.is_production:
            raise HTTPException(
                status_code=400,
                detail="callback_url ditolak: CALLBACK_ALLOWED_HOSTS belum diisi di production.",
            )
        return
    if bagian.hostname.lower() not in izin:
        raise HTTPException(
            status_code=400,
            detail=f"Host callback '{bagian.hostname}' tidak ada di CALLBACK_ALLOWED_HOSTS.",
        )


# Engine memuat model secara lazy. Lock memastikan satu dokumen diproses dalam satu waktu:
# Docling/Paddle/Ollama lokal tidak aman dan tidak lebih cepat bila dipanggil paralel di satu mesin.
engine = OpenADEEngine()
_engine_lock = threading.Lock()
ALLOWED_SUFFIXES = {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _check_ollama_status() -> dict:
    """Cek konektivitas ke Ollama server secara cepat (timeout 2s)."""
    ollama_url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/tags"
    try:
        req = urllib.request.Request(ollama_url, headers={"User-Agent": "OpenADE-HealthCheck"})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                model_ready = any(config.OLLAMA_MODEL in m for m in models)
                return {
                    "online": True,
                    "target_model": config.OLLAMA_MODEL,
                    "target_model_installed": model_ready,
                    "available_models": models,
                }
    except Exception as e:
        return {
            "online": False,
            "target_model": config.OLLAMA_MODEL,
            "target_model_installed": False,
            "error": str(e),
        }
    return {"online": False, "target_model": config.OLLAMA_MODEL, "target_model_installed": False}


def _save_upload(file: UploadFile) -> Path:
    """Nama file dari klien tidak dipakai sebagai path (mencegah path traversal & tabrakan nama)."""
    original = Path(file.filename or "upload.pdf").name
    suffix = Path(original).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400, detail=f"Format tidak didukung: {suffix or '(tanpa ekstensi)'}"
        )
    upload_dir = config.TEMP_UPLOADS / uuid.uuid4().hex
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / original
    # Batas ukuran diperiksa SAMBIL menulis, bukan lewat header Content-Length: header itu
    # dikirim klien dan bisa berbohong. Tanpa batas, satu unggahan besar bisa memenuhi disk.
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    with open(target, "wb") as buffer:
        while chunk := file.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                buffer.close()
                shutil.rmtree(upload_dir, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"Berkas melebihi batas {config.MAX_UPLOAD_MB} MB.",
                )
            buffer.write(chunk)
    return target


def _cleanup(path: Path) -> None:
    shutil.rmtree(path.parent, ignore_errors=True)


@app.get("/", tags=["System & Health"], summary="Root status service")
def read_root():
    return {
        "status": "online",
        "service": "Open ADE Document AI Service",
        "version": "1.1.0",
        "model": config.OLLAMA_MODEL,
        "engine": "Docling + PaddleOCR + Ollama",
        "docs_url": "/docs",
    }


@app.get("/health", tags=["System & Health"], summary="Health check endpoint")
@app.get("/api/v1/health", tags=["System & Health"], summary="API v1 Health check endpoint")
def health_check():
    ollama_info = _check_ollama_status()
    storage_ok = config.TEMP_UPLOADS.exists() and config.OUTPUT_DIR.exists()
    status = (
        "healthy"
        if (storage_ok and ollama_info.get("online"))
        else "degraded"
        if storage_ok
        else "unhealthy"
    )
    return {
        "status": status,
        "storage": {
            "ready": storage_ok,
            "temp_dir": str(config.TEMP_UPLOADS),
            "output_dir": str(config.OUTPUT_DIR),
        },
        "ollama": ollama_info,
        "version": {
            "parser_version": config.PARSER_VERSION,
            "schema_version": config.SCHEMA_VERSION,
            "dol_schema_version": config.DOL_SCHEMA_VERSION,
        },
    }


@app.get(
    "/api/v1/info",
    dependencies=[Depends(require_api_key)],
    tags=["System & Health"],
    summary="Informasi konfigurasi dan kapabilitas API",
)
def get_system_info():
    return {
        "service": "Open ADE (Autonomous Document Extraction)",
        "allowed_file_types": sorted(ALLOWED_SUFFIXES),
        "supported_document_types": ["CONTRACT", "SPK", "SPH", "BAST", "AUTO"],
        "ollama_config": {
            "model": config.OLLAMA_MODEL,
            "context_window": config.OLLAMA_NUM_CTX,
            "base_url": config.OLLAMA_BASE_URL,
            "temperature": config.OLLAMA_TEMPERATURE,
        },
        "parser_config": {
            "ocr_engine_default": config.OCR_ENGINE,
            "ocr_engine_choices": ["rapidocr", "mac", "tesseract", "paddle", "auto"],
            "ocr_lang": config.OCR_LANG,
            "default_dpi": config.DEFAULT_DPI,
            "scanned_char_threshold": config.SCANNED_CHAR_THRESHOLD,
        },
        "nocodb": {
            "url": config.NOCODB_URL,
            "push_enabled": config.NOCODB_PUSH_ENABLED,
            "token_configured": bool(config.NOCODB_API_TOKEN),
            "tables_mapped": sorted(config.nocodb_table_ids()),
        },
    }


@app.post(
    "/api/v1/parse",
    dependencies=[Depends(require_api_key)],
    tags=["Document Parsing"],
    summary="Parse dokumen ke format LandingAI Compatible Markdown & JSON IR",
    description="Mengekstrak layout, tabel, dan struktur teks dari file PDF/DOCX/Gambar "
    "menggunakan Docling / PaddleOCR.",
)
def parse_document_endpoint(
    file: UploadFile = File(..., description="File PDF/DOCX/Gambar dokumen yang ingin diproses"),
    max_pages: int = Form(None, description="Batas maksimal halaman (opsional)"),
    ocr: str = Form(
        None,
        description="Mesin OCR: 'rapidocr' (default), 'mac', 'tesseract', atau 'paddle'",
    ),
):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            return engine.parse(str(temp_path), max_pages=max_pages, ocr=ocr).model_dump()
    except HTTPException:
        raise
    except Exception as e:
        raise _galat_internal(e) from e
    finally:
        _cleanup(temp_path)


@app.post(
    "/api/v1/extract",
    dependencies=[Depends(require_api_key)],
    tags=["Document Extraction"],
    summary="Ekstraksi data terstruktur dari teks Markdown",
    description="Mengekstrak field dan tabel menggunakan model LLM Ollama (Qwen 2.5) dengan skema "
    "Pydantic.",
)
def extract_document_endpoint(
    markdown_text: str = Form(..., description="Teks hasil parsing dokumen dalam format Markdown"),
    doc_type: str = Form(
        "auto", description="Tipe dokumen: 'auto', 'contract', 'sph', atau 'bast'"
    ),
):
    try:
        with _engine_lock:
            extracted, resolved_type = engine.extract(markdown_text, doc_type=doc_type)
        return {"document_type": resolved_type, "data": extracted.model_dump(by_alias=True)}
    except Exception as e:
        raise _galat_internal(e) from e


def _build_api_response(result: dict, source_path: Path = None) -> dict:
    """
    Satu bentuk respons untuk jalur sinkron DAN jalur job asinkron.

    Sebelumnya bentuk ini dirakit di dalam endpoint, jadi jalur kedua mana pun berisiko
    mengirim bentuk yang sedikit berbeda ke n8n -- dan n8n baru memberi tahu lewat node
    yang gagal, bukan lewat pesan yang jelas.

    `companion_payload` adalah bentuk yang dipakai NocoDB (tabel dol_schema, nama teknis
    kolom). Pada arsitektur briefing (hlm. 8) n8n yang mem-POST-nya, FastAPI hanya
    mengembalikannya. Untuk jenis dokumen yang tabelnya belum berlaku (BAST), nilainya None
    dan alasannya ada di `companion_catatan`.

    `source_path` harus dipanggil selagi berkas unggahan masih ada: kunci idempotensi
    companion dihitung dari byte berkas itu.
    """
    ringkas = {
        "document_name": Path(result["files"]["extract_json"]).stem.replace(".extract", ""),
        "document_type": result["document_type"],
        "data": result["extracted"].model_dump(by_alias=True),
        "validation": result["validation"].model_dump(mode="json"),
        "evidence": [e.model_dump(mode="json") for e in result["evidence"]],
        "quality_report": result["quality_report"],
        "run_info": result["run_info"],
        "markdown": result["parsed"].markdown,
    }
    return {
        "status": "success",
        "document_type": result["document_type"],
        "extracted_data": result["extracted"].model_dump(by_alias=True),
        "validation": result["validation"].model_dump(mode="json"),
        "evidence": [e.model_dump(mode="json") for e in result["evidence"]],
        "quality_report": result["quality_report"],
        "run_info": result["run_info"],
        "metadata": result["parsed"].metadata.model_dump(),
        "files": result["files"],
        "companion_payload": build_companion_payload(ringkas, source_path),
        "companion_catatan": payload_note(result["document_type"]),
        "nocodb_push": None,
    }


def _cek_push_aktif() -> None:
    if not config.NOCODB_PUSH_ENABLED:
        raise HTTPException(
            status_code=409,
            detail="push_to_nocodb diminta tapi NOCODB_PUSH_ENABLED=false. "
            "Aktifkan env NOCODB_PUSH_ENABLED=1 secara sadar sebelum menulis ke NocoDB.",
        )


def _push_to_nocodb(companion_payload: dict) -> dict:
    """Jalur alternatif tanpa n8n. Harus diaktifkan sadar lewat NOCODB_PUSH_ENABLED=1."""
    _cek_push_aktif()
    if not companion_payload:
        raise HTTPException(
            status_code=422,
            detail="Jenis dokumen ini tidak dikirim ke NocoDB (lihat companion_catatan); "
            "tidak ada yang dikirim.",
        )
    from app.companion.nocodb_push import CompanionPusher, CompanionPushError

    try:
        pusher = CompanionPusher(
            config.NOCODB_URL, config.NOCODB_API_TOKEN, config.nocodb_table_ids()
        )
        return pusher.push(companion_payload)
    except CompanionPushError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.post(
    "/api/v1/process-all",
    dependencies=[Depends(require_api_key)],
    tags=["End-to-End Pipeline"],
    summary="Proses End-to-End Dokumen (Parse + Extract + Validate + Evidence Grounding)",
    description="Menjalankan seluruh pipeline: Ingestion/Profiling, Layout Parsing, LLM "
    "Extraction, Business Rule Validation, dan Visual Grounding.",
)
def process_full_endpoint(
    file: UploadFile = File(..., description="File PDF/DOCX/Gambar yang ingin diproses"),
    doc_type: str = Form(
        "auto", description="Tipe dokumen: 'auto', 'contract', 'sph', atau 'bast'"
    ),
    max_pages: int = Form(None, description="Batas maksimal halaman (opsional)"),
    ocr: str = Form(
        None,
        description="Mesin OCR: 'rapidocr' (default), 'mac', 'tesseract', atau 'paddle'",
    ),
    push_to_nocodb: bool = Form(
        False,
        description="Kirim hasil ke NocoDB. Default mati: pada arsitektur briefing, n8n yang "
        "mengorkestrasi push.",
    ),
):
    temp_path = _save_upload(file)
    try:
        with _engine_lock:
            result = engine.process_full(
                str(temp_path), doc_type=doc_type, max_pages=max_pages, ocr=ocr
            )
        response = _build_api_response(result, temp_path)
        if push_to_nocodb:
            response["nocodb_push"] = _push_to_nocodb(response["companion_payload"])
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise _galat_internal(e) from e
    finally:
        _cleanup(temp_path)


# Integrasi n8n (jalur asinkron): POST /api/v1/jobs -> callback_url atau polling
# GET /api/v1/jobs/{job_id}. Rangkaian node lengkap ada di docs/INTEGRASI_N8N.md.


def _run_job(job: Job) -> dict:
    """Dijalankan di worker thread. Membersihkan berkas unggahan apa pun hasilnya."""
    try:
        return _run_job_inti(job)
    except Exception as e:
        if not config.is_production:
            raise
        # job.error ikut dikirim ke callback dan GET /jobs; di production cukup jenis galat
        # dan nomor job untuk mencari jejak lengkapnya di log.
        logger.exception(f"Job {job.job_id} gagal: {type(e).__name__}: {e}")
        raise RuntimeError(
            f"Pemrosesan gagal ({type(e).__name__}). Rujukan log: job {job.job_id}"
        ) from None
    finally:
        _cleanup(job.source_path)


def _run_job_inti(job: Job) -> dict:
    with _engine_lock:
        result = engine.process_full(
            str(job.source_path),
            doc_type=job.doc_type,
            max_pages=job.max_pages,
            ocr=job.ocr,
        )
    response = _build_api_response(result, job.source_path)
    # document_id dari isi berkas harus cocok dengan yang sudah dibalas ke n8n di awal;
    # kalau tidak, kunci idempotensi yang dipegang n8n bukan kunci yang masuk NocoDB.
    actual = (response.get("run_info") or {}).get("document_id")
    if actual and actual != job.document_id:
        logger.warning(
            f"document_id job {job.job_id} berubah: dibalas '{job.document_id}', "
            f"hasil pipeline '{actual}'. Memakai hasil pipeline."
        )
        job.document_id = actual
    if job.push_to_nocodb:
        # Push yang gagal tidak menggagalkan job: hasil ekstraksi 3-5 menit tetap bisa
        # diambil dan dikirim ulang. Statusnya ikut di ringkasan job, supaya n8n yang
        # hanya polling tanpa include_result tetap tahu barisnya belum masuk.
        try:
            response["nocodb_push"] = _push_to_nocodb(response["companion_payload"])
            job.nocodb_push = "ok"
        except Exception as e:
            sebab = e.detail if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}"
            response["nocodb_push"] = {"error": sebab}
            job.nocodb_push = f"gagal: {sebab}"
            logger.error(f"Push NocoDB job {job.job_id} gagal: {sebab}")
    return response


def _notify_callback(job: Job) -> str:
    """
    Kirim hasil ke webhook n8n. Kegagalan di sini TIDAK menggagalkan job: hasilnya tetap
    bisa diambil lewat GET /api/v1/jobs/{job_id}, jadi kerja 3-5 menit tidak terbuang
    hanya karena n8n kebetulan sedang restart.
    """
    body = {
        "job_id": job.job_id,
        "document_id": job.document_id,
        "filename": job.filename,
        "status": job.status,
        "duration_s": job.duration_s,
        "error": job.error,
        "result": job.result,
    }
    # Body diserialisasi sendiri supaya HMAC dihitung atas byte yang persis dikirim.
    isi = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.CALLBACK_SECRET:
        tanda = hmac.new(config.CALLBACK_SECRET.encode(), isi, hashlib.sha256).hexdigest()
        headers["X-OpenADE-Signature"] = f"sha256={tanda}"
    with httpx.Client(timeout=config.CALLBACK_TIMEOUT) as client:
        resp = client.post(job.callback_url, content=isi, headers=headers)
        return f"{resp.status_code}"


job_queue = JobQueue(runner=_run_job, notifier=_notify_callback)


@app.post(
    "/api/v1/jobs",
    status_code=202,
    dependencies=[Depends(require_api_key)],
    tags=["Integrasi n8n"],
    summary="Kirim dokumen untuk diproses (asinkron) — endpoint utama untuk n8n",
    description=(
        "Membalas SEKETIKA dengan job_id dan document_id, lalu memproses di latar. "
        "Satu dokumen butuh 160-310 detik, jadi jalur sinkron /api/v1/process-all akan "
        "timeout di n8n. `document_id` adalah sha256 isi berkas: kirim PDF yang sama dua "
        "kali dan nilainya sama, sehingga push ke NocoDB menjadi UPDATE, bukan baris kembar."
    ),
)
def submit_job_endpoint(
    file: UploadFile = File(..., description="File PDF/DOCX/Gambar yang ingin diproses"),
    doc_type: str = Form(
        "auto", description="Tipe dokumen: 'auto', 'contract', 'sph', atau 'bast'"
    ),
    max_pages: int = Form(None, description="Batas maksimal halaman (opsional)"),
    ocr: str = Form(
        None, description="Mesin OCR: 'rapidocr' (default), 'mac', 'tesseract', 'paddle'"
    ),
    callback_url: str = Form(
        None, description="URL webhook n8n. Hasil lengkap di-POST ke sini saat selesai."
    ),
    push_to_nocodb: bool = Form(
        False,
        description="Tulis companion_payload ke NocoDB setelah ekstraksi selesai. Butuh "
        "NOCODB_PUSH_ENABLED=1; bila mati, permintaan ditolak 409 sebelum masuk antrian.",
    ),
):
    # Ditolak sekarang, bukan setelah 3-5 menit ekstraksi.
    _cek_callback_url(callback_url)
    if push_to_nocodb:
        _cek_push_aktif()
    temp_path = _save_upload(file)
    try:
        document_id = content_hash(temp_path)
    except OSError as e:
        _cleanup(temp_path)
        raise HTTPException(status_code=400, detail=f"Berkas tidak terbaca: {e}") from e

    job = Job(
        job_id=new_job_id(),
        document_id=document_id,
        filename=temp_path.name,
        doc_type=doc_type,
        ocr=ocr,
        max_pages=max_pages,
        callback_url=callback_url,
        source_path=temp_path,
        push_to_nocodb=push_to_nocodb,
    )
    job_queue.submit(job)
    return {
        "job_id": job.job_id,
        "document_id": job.document_id,
        # Konstanta, bukan `job.status`: worker bisa saja sudah mengambil job ini sebelum
        # respons selesai dirakit, dan n8n tidak boleh menerima status yang berubah-ubah
        # untuk balasan 202 yang artinya "diterima dan diantrikan".
        "status": QUEUED,
        "queue_position": job_queue.queue_position(job.job_id),
        "filename": job.filename,
        "callback_url": callback_url,
        "push_to_nocodb": push_to_nocodb,
        "poll_url": f"/api/v1/jobs/{job.job_id}",
        "catatan": (
            "Perkiraan 160-310 detik per dokumen. Poll tiap 30 detik, atau pakai callback_url."
        ),
    }


@app.get(
    "/api/v1/jobs/{job_id}",
    dependencies=[Depends(require_api_key)],
    tags=["Integrasi n8n"],
    summary="Status satu job; sertakan include_result=true untuk mengambil hasilnya",
)
def get_job_endpoint(
    job_id: str,
    include_result: bool = False,
):
    job = job_queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' tidak ditemukan.")
    body = job.summary()
    body["queue_position"] = job_queue.queue_position(job_id)
    if include_result and job.status in (DONE, FAILED):
        body["result"] = job.result
    # 200 untuk semua status: status job ada di body. Memakai kode HTTP untuk membedakan
    # "belum selesai" membuat node n8n menandainya sebagai error dan menghentikan alur.
    return JSONResponse(status_code=200, content=body)


@app.get(
    "/api/v1/jobs",
    dependencies=[Depends(require_api_key)],
    tags=["Integrasi n8n"],
    summary="Daftar job terbaru + ringkasan antrian",
)
def list_jobs_endpoint(limit: int = 50, document_id: str = None):
    if document_id:
        return {
            "stats": job_queue.stats(),
            "jobs": [j.summary() for j in job_queue.find_by_document(document_id)],
        }
    return {"stats": job_queue.stats(), "jobs": job_queue.list_jobs(limit=limit)}


if __name__ == "__main__":
    # Memproses satu dokumen dari terminal: pakai run.py, bukan berkas ini.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=os.getenv("UVICORN_RELOAD", "0") == "1",
    )
