"""
Open ADE — Antrian job asinkron untuk pemanggilan dari n8n.

**Kenapa ini ada.** Benchmark di `storage/outputs/bench/_hasil/_ringkasan.json` mencatat
162-313 detik per dokumen (OCR + 2-3 panggilan LLM). Node HTTP Request di n8n punya timeout
default 300 detik, dan proxy di depannya (nginx/Cloudflare) umumnya 60-100 detik. Endpoint
sinkron `/api/v1/process-all` karena itu akan **timeout pada dokumen normal**, bukan pada
kasus ekstrem -- dan n8n akan mengulang kirim, menumpuk antrian pada mesin yang sama.

Pola yang dipakai di sini adalah pola standar untuk kerja panjang:

    n8n  --POST /api/v1/jobs-->  202 {job_id, document_id}   (balas SEKETIKA)
                                        |
                                 worker memproses
                                        |
    n8n  <--POST callback_url---  hasil lengkap + companion_payload
      atau
    n8n  --GET /api/v1/jobs/{id}-->  polling sampai status "done"

`document_id` dikembalikan **di respons pertama**, sebelum dokumen diproses, karena ia
dihitung dari sha256 isi berkas. Jadi n8n bisa langsung memakainya sebagai kunci idempotensi:
mengirim PDF yang sama dua kali menghasilkan `document_id` yang sama, dan push NocoDB-nya
menjadi UPDATE, bukan baris kembar.

**Satu worker, sengaja.** Docling/PaddleOCR/Ollama lokal tidak lebih cepat bila dipanggil
paralel di satu mesin -- mereka berebut CPU yang sama dan justru saling memperlambat. Antrian
membuat perilaku itu eksplisit dan terukur (`queue_position`), bukan tersembunyi di balik lock
yang membuat n8n menunggu tanpa tahu sedang menunggu apa.

**Batas yang diketahui.** Status job disimpan di memori proses. Restart uvicorn = job yang
sedang berjalan hilang. Untuk satu instance dengan satu worker ini memadai; bila nanti perlu
tahan-restart, penggantinya adalah Redis + RQ/Celery, bukan menambal dict ini.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.logger import logger

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"


@dataclass
class Job:
    job_id: str
    document_id: str
    filename: str
    doc_type: str
    ocr: str | None
    max_pages: int | None
    callback_url: str | None
    source_path: Path
    status: str = QUEUED
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    started_at: str | None = None
    finished_at: str | None = None
    duration_s: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    callback_status: str | None = None
    push_to_nocodb: bool = False
    nocodb_push: str | None = None  # "ok" / "gagal: <sebab>"; None bila push tidak diminta

    def summary(self) -> dict[str, Any]:
        """Status tanpa hasil — ini yang dipoll n8n; hasil penuhnya bisa puluhan MB."""
        return {
            "job_id": self.job_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "error": self.error,
            "callback_status": self.callback_status,
            "nocodb_push": self.nocodb_push,
        }


class JobQueue:
    """
    Antrian FIFO dengan satu worker thread.

    `runner` disuntikkan (bukan meng-import engine langsung) supaya modul ini bisa diuji
    tanpa memuat Docling/Ollama — memuat keduanya butuh belasan detik dan ratusan MB.
    """

    def __init__(
        self,
        runner: Callable[[Job], dict[str, Any]],
        notifier: Callable[[Job], str] | None = None,
        max_history: int = 200,
    ):
        self._runner = runner
        self._notifier = notifier
        self._max_history = max_history
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._q: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    # ------------------------------------------------------------------ siklus hidup
    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, name="openade-worker", daemon=True)
        self._worker.start()
        logger.info("🧵 Worker antrian job dijalankan (1 dokumen pada satu waktu)")

    def submit(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
            self._evict_locked()
        self._q.put(job.job_id)
        logger.info(f"📥 Job {job.job_id} masuk antrian ({job.filename}, doc {job.document_id})")
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_by_document(self, document_id: str) -> list[Job]:
        """Dipakai n8n untuk mengecek apakah berkas ini pernah/sedang diproses."""
        with self._lock:
            return [j for j in self._jobs.values() if j.document_id == document_id]

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._order[-limit:][::-1]
            return [self._jobs[i].summary() for i in ids if i in self._jobs]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            jobs = list(self._jobs.values())
        return {
            "queued": sum(1 for j in jobs if j.status == QUEUED),
            "running": sum(1 for j in jobs if j.status == RUNNING),
            "done": sum(1 for j in jobs if j.status == DONE),
            "failed": sum(1 for j in jobs if j.status == FAILED),
            "worker_alive": bool(self._worker and self._worker.is_alive()),
        }

    def queue_position(self, job_id: str) -> int | None:
        """Berapa job antre di depannya. Dipakai n8n untuk mengatur jeda polling."""
        with self._lock:
            waiting = [i for i in self._order if i in self._jobs and self._jobs[i].status == QUEUED]
        return waiting.index(job_id) if job_id in waiting else None

    # ------------------------------------------------------------------ internal
    def _evict_locked(self) -> None:
        """Buang job lama yang sudah selesai; job antre/berjalan tidak pernah dibuang."""
        while len(self._order) > self._max_history:
            for idx, jid in enumerate(self._order):
                job = self._jobs.get(jid)
                if job is None or job.status in (DONE, FAILED):
                    self._order.pop(idx)
                    self._jobs.pop(jid, None)
                    break
            else:
                return  # semua masih antre/berjalan — jangan buang apa pun

    def _loop(self) -> None:
        while True:
            job_id = self._q.get()
            job = self.get(job_id)
            if job is None:
                self._q.task_done()
                continue
            t0 = time.time()
            job.status = RUNNING
            job.started_at = datetime.now(UTC).isoformat(timespec="seconds")
            try:
                job.result = self._runner(job)
                job.status = DONE
            except Exception as e:
                # Satu job gagal tidak boleh mematikan worker: job berikutnya harus tetap jalan.
                job.status = FAILED
                job.error = f"{type(e).__name__}: {e}"
                logger.error(f"❌ Job {job.job_id} gagal: {job.error}\n{traceback.format_exc()}")
            finally:
                job.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
                job.duration_s = round(time.time() - t0, 2)
                if job.callback_url and self._notifier:
                    try:
                        job.callback_status = self._notifier(job)
                    except Exception as e:
                        job.callback_status = f"gagal: {type(e).__name__}: {e}"
                        logger.error(f"❌ Callback job {job.job_id} gagal: {e}")
                self._q.task_done()
                logger.info(f"✅ Job {job.job_id} {job.status} dalam {job.duration_s}s")


def new_job_id() -> str:
    return f"job-{uuid.uuid4().hex[:12]}"
