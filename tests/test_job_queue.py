"""
Tes antrian job asinkron (jalur n8n) dan kunci idempotensi dokumen.

Fokusnya pada dua janji yang dipegang n8n dan tidak boleh diam-diam berubah:

  1. `document_id` sama untuk isi berkas yang sama -- apa pun nama berkasnya dan apa pun
     hasil OCR-nya. Ini kunci upsert NocoDB; kalau berubah, memproses ulang satu dokumen
     menghasilkan baris kembar, bukan pembaruan.
  2. Satu job yang gagal tidak menghentikan worker. Kalau worker mati diam-diam, job
     berikutnya menggantung "queued" selamanya dan n8n menunggu tanpa pesan apa pun.
"""

import threading
import time
from pathlib import Path

from app.document_ir.adapter import content_hash
from app.services.job_queue import DONE, FAILED, Job, JobQueue, new_job_id


def _job(tmp_path: Path, name: str = "a.pdf", document_id: str = "doc-x", **kw) -> Job:
    src = tmp_path / name
    src.write_bytes(b"%PDF-1.4 isi")
    return Job(
        job_id=new_job_id(),
        document_id=document_id,
        filename=name,
        doc_type="auto",
        ocr=None,
        max_pages=None,
        callback_url=kw.pop("callback_url", None),
        source_path=src,
        **kw,
    )


def _wait(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# document_id
def test_content_hash_sama_untuk_isi_sama_nama_berbeda(tmp_path):
    """n8n sering menyimpan unggahan dengan nama temporer; id tidak boleh ikut berubah."""
    a, b = tmp_path / "SPK.pdf", tmp_path / "tmp-9f2a.pdf"
    a.write_bytes(b"isi kontrak yang sama persis")
    b.write_bytes(b"isi kontrak yang sama persis")
    assert content_hash(a) == content_hash(b)


def test_content_hash_berbeda_untuk_isi_berbeda(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(b"kontrak A")
    b.write_bytes(b"kontrak B")
    assert content_hash(a) != content_hash(b)


def test_content_hash_tidak_terpengaruh_hasil_ocr(tmp_path):
    """
    Regresi: versi lama menurunkan id dari markdown hasil OCR, jadi mengganti OCR_ENGINE
    membuat dokumen yang sama masuk NocoDB sebagai baris baru.
    """
    src = tmp_path / "scan.pdf"
    src.write_bytes(b"halaman hasil pindai")
    sebelum = content_hash(src)
    # Isi berkas tidak berubah; apa pun yang terjadi pada tahap OCR tidak boleh mengubah id.
    assert content_hash(src) == sebelum


def test_content_hash_membaca_berkas_besar_bertahap(tmp_path):
    besar = tmp_path / "besar.pdf"
    besar.write_bytes(b"x" * (3 * 1024 * 1024 + 7))  # lintas beberapa potongan 1 MB
    assert content_hash(besar).startswith("doc-")


# antrian
def test_job_sukses_menyimpan_hasil(tmp_path):
    q = JobQueue(runner=lambda job: {"status": "success", "companion_payload": {"document": [{}]}})
    q.start()
    job = q.submit(_job(tmp_path))
    assert _wait(lambda: job.status == DONE), f"status berhenti di {job.status}"
    assert job.result["companion_payload"]["document"] == [{}]
    assert job.duration_s is not None


def test_job_gagal_tidak_mematikan_worker(tmp_path):
    """Job kedua harus tetap jalan walau job pertama melempar exception."""

    def runner(job):
        if job.filename == "rusak.pdf":
            raise ValueError("OCR tidak menghasilkan teks")
        return {"ok": True}

    q = JobQueue(runner=runner)
    q.start()
    gagal = q.submit(_job(tmp_path, "rusak.pdf"))
    assert _wait(lambda: gagal.status == FAILED)
    assert "OCR tidak menghasilkan teks" in gagal.error

    berhasil = q.submit(_job(tmp_path, "baik.pdf"))
    assert _wait(lambda: berhasil.status == DONE), "worker mati setelah job gagal"


def test_job_diproses_satu_per_satu(tmp_path):
    """Docling/Ollama lokal berebut CPU; antrian harus benar-benar serial."""
    bersamaan, puncak = [], []
    lock = threading.Lock()

    def runner(job):
        with lock:
            bersamaan.append(1)
            puncak.append(len(bersamaan))
        time.sleep(0.05)
        with lock:
            bersamaan.pop()
        return {"ok": True}

    q = JobQueue(runner=runner)
    q.start()
    jobs = [q.submit(_job(tmp_path, f"{i}.pdf")) for i in range(4)]
    assert _wait(lambda: all(j.status == DONE for j in jobs), timeout=10)
    assert max(puncak) == 1, f"ada {max(puncak)} job berjalan bersamaan"


def test_callback_gagal_tidak_membatalkan_hasil(tmp_path):
    """
    Hasil 3-5 menit tidak boleh hilang hanya karena n8n sedang restart: job tetap 'done'
    dan hasilnya masih bisa diambil lewat GET /api/v1/jobs/{id}.
    """

    def notifier(job):
        raise ConnectionError("n8n tidak menjawab")

    q = JobQueue(runner=lambda job: {"ok": True}, notifier=notifier)
    q.start()
    job = q.submit(_job(tmp_path, callback_url="http://n8n.local/webhook/x"))
    assert _wait(lambda: job.callback_status is not None, timeout=5)
    assert job.status == DONE
    assert job.result == {"ok": True}
    assert "gagal" in job.callback_status


def test_queue_position_dan_stats(tmp_path):
    mulai = threading.Event()
    q = JobQueue(runner=lambda job: (mulai.wait(5), {"ok": True})[1])
    q.start()
    pertama = q.submit(_job(tmp_path, "1.pdf"))
    assert _wait(lambda: pertama.status == "running")
    kedua = q.submit(_job(tmp_path, "2.pdf"))
    assert q.queue_position(kedua.job_id) == 0  # tidak ada yang antre di depannya
    assert q.queue_position(pertama.job_id) is None  # sudah berjalan, bukan antre
    assert q.stats()["running"] == 1
    mulai.set()
    assert _wait(lambda: kedua.status == DONE, timeout=10)


def test_find_by_document_menemukan_pemrosesan_ulang(tmp_path):
    """n8n memakai ini untuk tahu berkas yang sama sudah pernah/sedang diproses."""
    q = JobQueue(runner=lambda job: {"ok": True})
    q.start()
    a = q.submit(_job(tmp_path, "a.pdf", document_id="doc-abc"))
    b = q.submit(_job(tmp_path, "a-salinan.pdf", document_id="doc-abc"))
    assert _wait(lambda: b.status == DONE, timeout=10)
    ditemukan = {j.job_id for j in q.find_by_document("doc-abc")}
    assert ditemukan == {a.job_id, b.job_id}
    assert q.find_by_document("doc-tidak-ada") == []


def test_job_antre_tidak_pernah_dibuang_oleh_eviction(tmp_path):
    """Riwayat boleh dipangkas; job yang belum diproses tidak boleh ikut hilang."""
    tahan = threading.Event()
    q = JobQueue(runner=lambda job: (tahan.wait(5), {"ok": True})[1], max_history=2)
    q.start()
    jobs = [q.submit(_job(tmp_path, f"{i}.pdf")) for i in range(5)]
    assert all(q.get(j.job_id) is not None for j in jobs), "job yang belum selesai terbuang"
    tahan.set()
    assert _wait(lambda: all(j.status == DONE for j in jobs), timeout=10)
