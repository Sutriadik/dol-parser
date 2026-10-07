"""
Open ADE — LLM-Powered Markdown Refiner (Qwen 2.5)

Modul pasca-parsing yang memanfaatkan penalaran LLM Qwen 2.5 untuk mereparasi
kerusakan OCR yang parah (misal teks rumpang/hilang karakter "Ber aal ana ea..."),
menyelaraskan hirarki heading, serta menjamin kerapian struktur tabel dan dokumen.

Aturan Ketat (Factual & Integrity Guard):
- TIDAK BOLEH mengubah nominal angka, harga, persentase, tanggal, atau nomor kontrak.
- TIDAK BOLEH merusak atau mengurangi baris/kolom tabel Markdown.
- Menghilangkan noise/artefak stempel basah atau tanda tangan yang tersisa.
"""

import re
import time

from app.config import config
from app.logger import logger
from app.parsers.text_cleaner import clean_ocr_text

MARKDOWN_REFINER_SYSTEM_PROMPT = """Anda adalah asisten AI spesialis Document Post-Processing & Text Restoration untuk dokumen kontrak, surat perintah kerja (SPK), surat penawaran harga (SPH), dan berita acara serah terima (BAST) berbahasa Indonesia.

Tugas Anda:
Perbaiki kesalahan penulisan (typo), kalimat rumpang akibat OCR scan (misalnya "Ber aal ana ea a a al a aan..." -> "Berdasarkan hasil negosiasi harga pada tanggal..."), spasi menempel, dan kerapian format Markdown dokumen.

ATURAN WAJIB:
1. PERBAIKI TYPO & KATA RUMPANG: Perbaiki ejaan bahasa Indonesia baku pada teks narasi/pasal/klausul yang rusak karena OCR tanpa mengubah maksud aslinya.
2. JAGA KEASLIAN DATA 100%: DILARANG KERAS mengubah, menambah, atau mengarang nominal harga, volume, tanggal, persentase PPN, nomor rekening, nama pihak, alamat, atau nomor dokumen.
3. STRUKTUR TABEL: Pertahankan seluruh tabel Markdown 2D. Jangan menghapus kolom atau baris tabel. Pastikan format tabel tetap rapi.
4. HILANGKAN NOISE: Hapus karakter sampah/artefak stempel atau tanda tangan acak (seperti "MmEto", "fer", "X", karakter acak 1-2 huruf tak bermakna).
5. FORMAT OUTPUT: Kembalikan HANYA teks dokumen Markdown hasil perbaikan tanpa kata pengantar, tanpa penjelasan, dan tanpa pembungkus ```markdown di luar dokumen.
"""


# Potongan rusak yang benar-benar ditemui pada scan tinta pudar. Daftar ini sengaja
# dipertahankan: ia bukan tebakan, melainkan sampel nyata yang bentuknya tidak tertangkap
# aturan umum di bawah (mis. "menn aan mannan" — katanya panjang, tapi bukan kata).
POTONGAN_RUSAK_TERAMATI = (
    "ber aal ana",
    "ea a a",
    "al a aan",
    "eran dan de da",
    "daleas an a a",
    "ssels e- as",
    "pean ean e e",
    "kamy i u s",
    "dva a aaa",
    "menn aan mannan",
)

# Berapa kata sangat pendek berturut-turut yang sudah tidak wajar.
#
# Ambang 5 ditetapkan dari kasus yang harus DILEWATKAN, bukan dari yang harus ditangkap.
# Diuji terhadap 25 dokumen nyata di storage/outputs/parsing/.
MIN_KATA_PENDEK_BERURUTAN = 5
PANJANG_KATA_PENDEK = 2

# Dua penanda yang membedakan teks sah dari kerusakan OCR, keduanya ditemukan dengan
# menguji 25 dokumen nyata di storage/outputs/parsing/ -- bukan dari dugaan:
#
#   TITIK menandai singkatan.  "S. H., M. H., M. Kn."  gelar notaris, ada di hampir
#                              setiap kontrak. OCR kerap menyisipkan spasi setelah titik,
#                              sehingga terbaca sebagai enam kata satu huruf.
#   ANGKA menandai kode.       "a100, a800, h100, l40s"  nama model perangkat. Kalau
#                              digitnya dibuang, "a100" menyusut jadi "a".
#
# Kerusakan OCR yang sesungguhnya tidak punya keduanya: "e d ad e a aa an", "a d n d an ad na".
_BERTITIK_ATAU_BERANGKA = re.compile(r"[.\d]")
_HURUF = re.compile(r"[^a-z]+")


def _runtun_kata_pendek_terpanjang(text_lower: str) -> int:
    """
    Runtun terpanjang kata beruntun yang isinya <= PANJANG_KATA_PENDEK huruf.

    Kata yang memuat titik atau angka dilewati, bukan dihitung nol: singkatan dan kode
    perangkat memang pendek, tetapi bukan tanda kerusakan.
    """
    terpanjang = berjalan = 0
    for token in text_lower.split():
        if _BERTITIK_ATAU_BERANGKA.search(token):
            continue
        huruf = _HURUF.sub("", token)
        if not huruf:
            continue  # garis tabel, tanda baca murni
        berjalan = berjalan + 1 if len(huruf) <= PANJANG_KATA_PENDEK else 0
        terpanjang = max(terpanjang, berjalan)
    return terpanjang


def _detect_severe_ocr_corruption(text: str) -> bool:
    """
    Apakah teks ini rusak parah sehingga layak ditulis ulang oleh LLM?

    Dua lapis, dan urutannya disengaja: sampel yang teramati lebih dulu (murah, pasti),
    baru aturan umum.

    Aturan umumnya mengukur SIFAT yang membuat teks itu rusak — deretan kata sangat pendek
    yang beruntun — bukan mencocokkan kata pemicu tertentu. Versi sebelumnya memakai daftar
    kata pemicu ("ea|al|aan|ana|sel|menn|pean"), sehingga kerusakan dengan bentuk sama
    tetapi huruf berbeda lolos begitu saja: "pean ean be do ke lo ba" tidak tertangkap
    hanya karena kata sesudah pemicunya kebetulan tiga huruf.
    """
    if not text:
        return False
    text_lower = text.lower()
    if any(p in text_lower for p in POTONGAN_RUSAK_TERAMATI):
        return True
    return _runtun_kata_pendek_terpanjang(text_lower) >= MIN_KATA_PENDEK_BERURUTAN


class MarkdownRefiner:
    """
    Refiner Markdown berbasis LLM Qwen 2.5 melalui Ollama API.
    """

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL.rstrip("/")
        self.model = config.OLLAMA_MODEL
        self._client = None  # dibuat sekali, bukan per halaman

    def refine_markdown(self, markdown_text: str, force: bool = False) -> str:
        """
        Membersihkan dan memoles Markdown hasil parsing menggunakan Qwen 2.5.

        Args:
            markdown_text: Raw/cleaned markdown dari Docling/PaddleOCR
            force: Jika True, jalankan LLM refiner tanpa memandang tingkat kerusakan OCR

        Returns:
            Markdown yang sudah dipoles rapi dan bebas kesalahan.
        """
        if not markdown_text or not markdown_text.strip():
            return markdown_text

        # 1. Jalankan deterministic cleaner terlebih dahulu
        cleaned_text = clean_ocr_text(markdown_text)

        # Jika LLM refiner dinonaktifkan di konfigurasi dan tidak di-force
        if not getattr(config, "ENABLE_LLM_MARKDOWN_REFINER", True) and not force:
            return cleaned_text

        # Cek apakah dokumen butuh LLM restoration (atau diproses per segmen)
        needs_llm = force or _detect_severe_ocr_corruption(cleaned_text)
        if not needs_llm:
            # Teks sudah sangat bersih dari deterministic cleaner
            return cleaned_text

        logger.info(
            "🧠 LLM Qwen 2.5 Markdown Refiner: Mendeteksi anomali OCR/kalimat rumpang, memoles teks..."
        )
        t0 = time.time()

        try:
            # Pecah dokumen per halaman jika terlalu panjang (>8000 karakter) agar Qwen fokus &
            # cepat
            pages = cleaned_text.split("<!-- PAGE BREAK -->")
            refined_pages = []

            for page in pages:
                page_clean = page.strip()
                if not page_clean:
                    refined_pages.append(page)
                    continue

                if _detect_severe_ocr_corruption(page_clean) or force:
                    refined_page = self._call_ollama_refine(page_clean)
                    refined_pages.append(refined_page)
                else:
                    refined_pages.append(page_clean)

            result = "\n\n<!-- PAGE BREAK -->\n\n".join(refined_pages)
            result = clean_ocr_text(result)
            logger.info(f"✅ LLM Markdown Refiner selesai ({time.time() - t0:.2f}s)")
            return result

        except Exception as e:
            logger.warning(
                f"⚠️  LLM Markdown Refiner gagal ({e}), fallback ke deterministic cleaner."
            )
            return cleaned_text

    def _call_ollama_refine(self, page_text: str) -> str:
        """Memanggil Ollama API untuk mereparasi teks halaman markdown."""
        import ollama

        prompt = f"Berikut teks dokumen Markdown yang perlu diperbaiki ejaan dan keterbacaannya:\n\n{page_text}\n\nKembalikan HANYA teks Markdown hasil perbaikan tanpa penjelasan apapun:"

        try:
            if self._client is None:
                self._client = ollama.Client(host=self.base_url, timeout=config.OLLAMA_TIMEOUT)
            resp = self._client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": MARKDOWN_REFINER_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                options={
                    "temperature": 0.0,
                    "seed": config.OLLAMA_SEED,
                    # num_ctx & keep_alive harus seragam dengan classifier/extractor, kalau
                    # tidak Ollama mengalokasi ulang konteks model di tengah pipeline.
                    "num_ctx": config.OLLAMA_NUM_CTX,
                    "num_predict": config.OLLAMA_NUM_PREDICT,
                },
                keep_alive=config.OLLAMA_KEEP_ALIVE,
            )
            content = resp.get("message", {}).get("content", "").strip()
            # Hapus markdown code fences jika model menyertakannya
            content = re.sub(r"^```(?:markdown)?\s*\n", "", content)
            content = re.sub(r"\n```\s*$", "", content)
            return content.strip() if content else page_text
        except Exception as e:
            logger.warning(f"Error calling Ollama for markdown refinement: {e}")
            return page_text
