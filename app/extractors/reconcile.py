"""
Open ADE: Rekonsiliasi deterministik hasil LLM dengan teks dokumen.

Fungsi murni yang mencocokkan keluaran LLM dengan sumber yang lebih bisa dipercaya: tabel
yang terbaca deterministik, nominal yang ditulis angka sekaligus terbilang, dan aritmetika
total SPH. Dipindah dari ollama_client.py (isi tidak berubah).
"""

import re
from typing import Any

from pydantic import BaseModel

from app.evidence.matcher import canon
from app.extractors.deterministic.dates import find_dates
from app.extractors.deterministic.numbers import (
    TARIF_PPN_UMUM,
    amounts_equal,
    confirmed_amounts,
    numbers_in_text,
    terbilang_to_number,
)
from app.logger import logger
from app.parsers.table_extractor import (
    clean_desc_and_extract_item_no,
    extract_tables_from_markdown,
    gabung_sel_gabungan,
)
from app.schemas.bast import BASTItemDetail
from app.schemas.sph import SPHExtractionSchema

SUMMARY_ROW = re.compile(
    r"^(?:sub\s*total|grand\s*total|total\s*[a-z0-9+]*|jumlah|jml|total\s*keseluruhan)\b",
    re.IGNORECASE,
)


# deterministic
# reconciliation
def reconcile_items(
    llm_items: list[BaseModel],
    table_items: list[dict[str, Any]],
    item_model: type,
    desc_attr: str,
    total_attr: str,
    total_alias: str,
    reference_amounts: list[float],
    table_groups: list[list[dict[str, Any]]] | None = None,
    markdown_text: str | None = None,
) -> list[BaseModel]:
    """
    Pilih daftar item dari tabel (deterministik) atau LLM berdasarkan kecocokan jumlah dengan
    subtotal/total, bukan sekadar jumlah baris. Field opsional dari LLM (spesifikasi, merek)
    disalin ke item tabel jika jumlah baris sama.

    `table_groups` = item yang sama dengan `table_items`, dikelompokkan per tabel asal.
    `markdown_text` = teks dokumen; tanpa itu tidak ada salinan tabel yang dipilih.
    """
    if not table_items:
        return llm_items

    # Rujukan pemilihan tabel adalah isian LLM, maka disaring dulu: hanya nominal yang
    # tertulis di dokumen yang boleh membuang tabel atau menggabung baris. Total tertulis yang
    # sudah termasuk PPN ikut dihitung, karena banyak kontrak tidak menulis subtotalnya.
    tertulis = [
        r for r in reference_amounts if r and markdown_text and nominal_tertulis(r, markdown_text)
    ]
    acuan_tertulis = tertulis + [r / (1 + t) for r in tertulis for t in TARIF_PPN_UMUM]

    def cocok_tertulis(amount: float) -> bool:
        return any(amounts_equal(amount, r, 1000.0) for r in acuan_tertulis)

    def matches_reference(amount: float) -> bool:
        return cocok_tertulis(amount) or any(
            ref and amounts_equal(amount, ref, 1000.0) for ref in reference_amounts
        )

    def jumlah(items: list[dict[str, Any]]) -> float:
        return sum(float(t.get(total_alias) or 0) for t in items)

    if acuan_tertulis and not matches_reference(jumlah(table_items)):
        # 1) Bundel kontrak (BA negosiasi + penawaran + nota pesanan) bisa memuat tabel harga
        #    yang sama beberapa kali; menggabungkan semuanya menggandakan item. Barisnya tidak
        #    selalu identik, jadi yang dipilih SATU tabel utuh yang jumlahnya cocok.
        # 2) Harga dari sel gabungan tersalin ke setiap sub-baris (gabung_sel_gabungan).
        kelompok = table_groups or [table_items]
        rapat = [gabung_sel_gabungan(g) for g in kelompok]
        kandidat: list[tuple[str, list[dict[str, Any]]]] = []
        if len(kelompok) > 1:
            kandidat += [("satu salinan tabel", g) for g in sorted(kelompok, key=len, reverse=True)]
        kandidat.append(("sub-baris sel gabungan disatukan", [i for g in rapat for i in g]))
        if len(rapat) > 1:
            kandidat += [
                ("satu salinan tabel, sub-baris sel gabungan disatukan", g)
                for g in sorted(rapat, key=len, reverse=True)
            ]
        pilihan = next(((alasan, k) for alasan, k in kandidat if cocok_tertulis(jumlah(k))), None)
        if pilihan:
            alasan, table_items = pilihan
            logger.info(
                f"Jumlah item tabel tidak cocok subtotal/total; memakai {len(table_items)} item "
                f"({alasan}) yang jumlahnya sama dengan nominal tertulis di dokumen"
            )

    table_ok = matches_reference(jumlah(table_items))
    llm_ok = matches_reference(sum(float(getattr(i, total_attr) or 0) for i in llm_items))
    llm_has_summary = any(SUMMARY_ROW.match(getattr(i, desc_attr).strip()) for i in llm_items)

    use_table = (table_ok and not llm_ok) or (
        table_ok == llm_ok and (len(table_items) >= len(llm_items) or llm_has_summary)
    )
    if not use_table:
        return llm_items

    try:
        validated = [item_model.model_validate(t) for t in table_items]
    except Exception as e:
        logger.warning(f"Item tabel tidak valid, tetap memakai item LLM: {e}")
        return llm_items
    if len(validated) == len(llm_items):
        for table_item, llm_item in zip(validated, llm_items):
            for attr in ("spesifikasi", "brand_merek", "nomor_part", "keterangan", "periode"):
                if hasattr(table_item, attr) and getattr(table_item, attr) is None:
                    setattr(table_item, attr, getattr(llm_item, attr, None))
    logger.info(
        f"Item dari tabel dipakai ({len(llm_items)} LLM → {len(validated)} tabel, "
        f"cocok subtotal={table_ok})"
    )
    return validated


def clean_and_number_items(items: list[BaseModel], desc_attr: str, no_attr: str) -> list[BaseModel]:
    cleaned = []
    for item in items:
        desc = (getattr(item, desc_attr) or "").strip()
        if SUMMARY_ROW.match(desc):
            continue
        clean_no, clean_text = clean_desc_and_extract_item_no(desc, getattr(item, no_attr) or "")
        setattr(item, desc_attr, clean_text)
        if clean_no and clean_no.isdigit():
            setattr(item, no_attr, clean_no)
        cleaned.append(item)
    for idx, item in enumerate(cleaned):
        if (getattr(item, no_attr) or "").strip() in ("", "0", "None", "null", "-"):
            setattr(item, no_attr, str(idx + 1))
    if len(cleaned) > 1 and len({getattr(i, no_attr) for i in cleaned}) == 1:
        for idx, item in enumerate(cleaned):
            setattr(item, no_attr, str(idx + 1))
    return cleaned


def find_terbilang_in_text(markdown_text: str, amount: float) -> str | None:
    """Cari kalimat terbilang di dokumen yang nilainya sama dengan nominal (deterministik)."""
    for match in re.finditer(
        r"\(\s*([A-Za-z][A-Za-z\s]{10,200}?(?:Rupiah|rupiah))\s*\)", markdown_text
    ):
        candidate = re.sub(r"\s+", " ", match.group(1)).strip()
        if terbilang_to_number(candidate) == int(round(amount)):
            return candidate
    return None


def fill_terbilang_from_text(extracted: BaseModel, markdown_text: str, amounts: tuple) -> None:
    """
    Isi `jumlah_terbilang` dari kalimat yang benar-benar tertulis di dokumen.

    `amounts` diurutkan dari yang paling diutamakan (total, lalu subtotal). Terbilang yang
    sudah cocok dengan salah satu nominal dibiarkan: dulu putaran subtotal menimpa terbilang
    total yang sudah benar bila dokumen menulis keduanya. Bila tidak ada kalimat yang
    nilainya sama, field dibiarkan apa adanya -- tidak ada terbilang hasil hitungan.
    """
    nominal = [int(round(a)) for a in amounts if a]
    if terbilang_to_number(extracted.jumlah_terbilang or "") in nominal:
        return
    for amount in nominal:
        found = find_terbilang_in_text(markdown_text, amount)
        if found:
            logger.info(f"Terbilang diambil dari teks dokumen: {found}")
            extracted.jumlah_terbilang = found
            return


# public API
def prefer_confirmed_amount(extracted: BaseModel, attr: str, markdown_text: str) -> None:
    """
    Ganti nominal LLM dengan nominal yang dikonfirmasi terbilangnya -- HANYA bila nominal
    LLM kosong atau tidak tertulis di dokumen, dan dokumen memuat tepat satu nominal
    terkonfirmasi.

    Kasus nyata (kontrak pindaian dua salinan): dokumen menulis nilai kontrak sebagai angka
    beserta terbilangnya, tetapi LLM mengembalikan 1.130.000.000 -- angka yang tidak ada di
    mana pun di dokumen. Nilai LLM yang tertulis di dokumen tidak pernah diganti: aturan ini
    hanya menambal karangan, tidak menebak ulang yang sudah terbukti.
    """
    confirmed = confirmed_amounts(markdown_text)
    if len(confirmed) != 1:
        return
    current = getattr(extracted, attr)
    in_document = current is not None and any(
        amounts_equal(current, n, 1.0) for n in numbers_in_text(markdown_text)
    )
    if in_document:
        return
    logger.warning(
        f"{attr}: nilai LLM {current} tidak tertulis di dokumen; memakai "
        f"{confirmed[0]:,.0f} yang dikonfirmasi oleh terbilangnya."
    )
    setattr(extracted, attr, confirmed[0])


def koreksi_subtotal_salah_kolom(
    extracted: Any, attr: str, items: list[BaseModel], total_attr: str, markdown_text: str
) -> None:
    """
    Ganti subtotal LLM yang terbaca dari kolom lain pada baris ringkasan tabel.

    Kasus nyata (SPH lisensi 12 bulan): baris "Subtotal (Sebelum PPN)" memuat jumlah kolom
    bulanan dan jumlah kolom total (12 kali lipatnya). LLM mengambil angka pertama,
    padahal jumlah item sama dengan angka kedua. Penggantian hanya terjadi bila kedua angka
    ada di baris ringkasan yang sama dan angka penggantinya sama dengan jumlah item, jadi
    nilainya tetap angka yang tertulis di dokumen, bukan hasil hitungan.
    """
    nilai = getattr(extracted, attr, None)
    totals = [getattr(i, total_attr, None) for i in items]
    if not nilai or not totals or any(t is None for t in totals):
        return
    jumlah_item = float(sum(totals))
    if amounts_equal(nilai, jumlah_item, 1.0):
        return
    for tabel in extract_tables_from_markdown(markdown_text):
        for baris in tabel["rows"]:
            sel = [str(v).strip() for k, v in baris.items() if not k.startswith("_")]
            label = next((v for v in sel if v), "")
            if not SUMMARY_ROW.match(label):
                continue
            angka = numbers_in_text(" | ".join(sel))
            if any(amounts_equal(nilai, a, 1.0) for a in angka) and any(
                amounts_equal(jumlah_item, a, 1.0) for a in angka
            ):
                logger.warning(
                    f"{attr}: nilai LLM {nilai:,.0f} diambil dari kolom lain pada baris "
                    f"'{label}'; memakai {jumlah_item:,.0f} di baris yang sama, yang sama dengan "
                    "jumlah item."
                )
                setattr(extracted, attr, jumlah_item)
                return


def reconcile_bast_items(
    llm_items: list[BASTItemDetail], table_items: list[dict[str, Any]]
) -> list[BASTItemDetail]:
    """
    BAST umumnya tidak punya kolom harga, jadi tidak ada nilai numerik untuk memvalidasi
    silang seperti pada kontrak/SPH (_reconcile_items). Pilih tabel hasil parsing
    deterministik jika baris tabelnya sama banyak/lebih banyak dari LLM, atau LLM sama
    sekali tidak menghasilkan item.
    """
    if not table_items or (llm_items and len(table_items) < len(llm_items)):
        return llm_items
    try:
        validated = [BASTItemDetail.model_validate(t) for t in table_items]
    except Exception as e:
        logger.warning(f"Item tabel BAST tidak valid, tetap memakai item LLM: {e}")
        return llm_items
    logger.info(f"Item BAST dari tabel dipakai ({len(llm_items)} LLM → {len(validated)} tabel)")
    return validated


def nominal_tertulis(nilai: float, markdown_text: str) -> bool:
    """Nominal itu tertulis di dokumen, sebagai angka atau sebagai terbilang."""
    if any(amounts_equal(nilai, n, 1.0) for n in numbers_in_text(markdown_text)):
        return True
    return find_terbilang_in_text(markdown_text, nilai) is not None


def drop_unwritten_sph_totals(ext: "SPHExtractionSchema", markdown_text: str) -> None:
    """
    Kosongkan Subtotal / Nilai PPN / Grand Total dari LLM yang tidak tertulis di dokumen,
    baik sebagai angka maupun terbilang. JANGAN menggantinya dengan hasil hitungan.

    Kasus nyata pada SPH jasa: tabel hanya menulis "Jumlah Setelah PPN", tanpa baris
    subtotal, tapi LLM mengisi Subtotal dengan angka yang tidak ada di mana pun di
    dokumen. Grounding memang menandainya UNSUPPORTED, tetapi nilainya tetap terkirim ke PM
    sebagai isian awal. Nilai kosong lebih jujur: PM melihatnya sebagai field hilang.
    """
    for attr in ("subtotal", "ppn_nominal", "grand_total"):
        nilai = getattr(ext, attr)
        if nilai is None or nominal_tertulis(nilai, markdown_text):
            continue
        logger.warning(
            f"{attr}: nilai LLM {nilai:,.0f} tidak tertulis di dokumen -> dikosongkan "
            "untuk diisi PM, tidak dihitung ulang."
        )
        setattr(ext, attr, None)


_PPN = r"(?:ppn|pajak\s+pertambahan\s+nilai(?:\s*\(\s*ppn\s*\))?)"
_PPN_PERSEN = r"(?:\s*(?P<persen>\d{1,2}(?:[.,]\d{1,2})?\s*%))?"
_BELUM_PPN = re.compile(
    rf"(?:(?:belum|tidak)\s+termasuk|sebelum|exclude|excl\.?)\s+{_PPN}{_PPN_PERSEN}", re.I
)
# Lookbehind: "termasuk PPN" di dalam "belum termasuk PPN" bukan pernyataan termasuk.
_TERMASUK_PPN = re.compile(
    rf"(?<!belum )(?<!tidak )(?:sudah\s+termasuk|termasuk|include|incl\.?|setelah)\s+{_PPN}"
    rf"{_PPN_PERSEN}",
    re.I,
)


def lengkapi_keterangan_ppn(ext: BaseModel, markdown_text: str, attr_persen: str) -> None:
    """
    Isi `keterangan_ppn` dengan kalimat yang menyatakan harga sudah/belum termasuk PPN,
    apa adanya ("belum termasuk PPN 11%"), dan tarif PPN yang kosong bila kalimat itu
    menuliskannya.

    Dikosongkan bila dokumen tidak menyebut PPN secara eksplisit ("sudah termasuk pajak-pajak"
    tidak dihitung) atau menyebut keduanya: tabel "Jumlah Sebelum PPN / Setelah PPN" sudah
    menuliskan subtotal dan total masing-masing, jadi keterangannya tidak menambah apa pun.
    """
    teks = re.sub(r"\s+", " ", markdown_text)
    belum, termasuk = list(_BELUM_PPN.finditer(teks)), list(_TERMASUK_PPN.finditer(teks))
    if bool(belum) == bool(termasuk):
        return
    temuan = belum or termasuk
    ext.keterangan_ppn = temuan[0].group(0).strip()
    persen = next((m.group("persen") for m in temuan if m.group("persen")), None)
    if persen and not getattr(ext, attr_persen):
        setattr(ext, attr_persen, re.sub(r"\s+", "", persen))


_HANYA_TANGGAL = re.compile(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}")


def _lipat(teks: str | None) -> str:
    return " ".join((teks or "").lower().split()).strip(" .,;")


def rapikan_dokumen_pendukung(ext: BaseModel) -> None:
    """
    Dokumen Pendukung kontrak: buang tanggal yang tersalin ke Nomor Dokumen, lalu isi Tanggal
    Negosiasi yang kosong dari Berita Acara Negosiasi yang tercantum di sana.

    Kasus nyata: konsiderans "Berita Acara Klarifikasi dan Negosiasi Harga <pekerjaan> tanggal
    24 Januari 2025". Nama pekerjaan memisahkan "Negosiasi" dari "tanggal", jadi petunjuk
    Tanggal Negosiasi tidak menangkapnya, dan LLM menaruh tanggal itu juga di Nomor Dokumen.
    Tanggal LLM yang sudah terisi tidak ditimpa.

    Syarat Lampiran Wajib BAST yang sama persis dengan salah satu Dokumen Pendukung juga
    dibuang: itu daftar surat konsiderans yang terbaca dua kali (kasus nyata: enam surat
    "Surat Nomor ... perihal ..." menjadi enam syarat lampiran BAST palsu). Suratnya tetap
    tersimpan di Dokumen Pendukung.
    """
    pendukung = ext.dokumen_pendukung or []
    if ext.syarat_lampiran_bast:
        konsiderans = {_lipat(d.nama_dokumen) for d in pendukung}
        sisa = [s for s in ext.syarat_lampiran_bast if _lipat(s) not in konsiderans]
        if len(sisa) != len(ext.syarat_lampiran_bast):
            logger.info(
                f"{len(ext.syarat_lampiran_bast) - len(sisa)} surat konsiderans dibuang dari "
                "Syarat Lampiran Wajib BAST"
            )
            ext.syarat_lampiran_bast = sisa or None
    for dok in pendukung:
        nomor = (dok.nomor_dokumen or "").strip()
        if _HANYA_TANGGAL.fullmatch(nomor) and find_dates(nomor):
            dok.nomor_dokumen = None
    if ext.tanggal_negosiasi:
        return
    for dok in pendukung:
        if re.search(r"negosiasi", dok.nama_dokumen or "", re.I) and dok.tanggal_dokumen:
            logger.info(f"Tanggal Negosiasi diambil dari Dokumen Pendukung: {dok.tanggal_dokumen}")
            ext.tanggal_negosiasi = dok.tanggal_dokumen
            return


def buang_salinan_contoh(ext: BaseModel, markdown_text: str, teks_prompt: str) -> list[str]:
    """
    Kosongkan field opsional yang isinya frasa dari CONTOH di prompt dan tidak ada di dokumen.
    Mengembalikan alias field yang dikosongkan.

    Kasus nyata (SPH BUT ke yayasan): Tujuan Surat terisi "Kepala Bagian Umum Dinas Contoh
    Kota Semarang", persis contoh SPH di prompt. Grounding memang menandainya UNSUPPORTED,
    tetapi nilai karangan tetap terkirim ke PM sebagai isian awal.

    Hanya frasa >= 4 kata: nilai pendek seperti "12 Bulan" atau "14 hari kalender" bisa
    kebetulan sama dengan contoh padahal benar (ditulis lain di dokumen). Field wajib tidak
    disentuh; statusnya tetap ditangani grounding.
    """
    di_prompt, di_dokumen = f" {canon(teks_prompt)} ", f" {canon(markdown_text)} "
    dibuang: list[str] = []

    def periksa(obj: BaseModel, awalan: str) -> None:
        for nama, info in type(obj).model_fields.items():
            nilai, alias = getattr(obj, nama), f"{awalan}{info.alias or nama}"
            if isinstance(nilai, BaseModel):
                periksa(nilai, f"{alias}.")
            elif isinstance(nilai, str) and not info.is_required():
                c = canon(nilai)
                if len(c.split()) >= 4 and f" {c} " in di_prompt and f" {c} " not in di_dokumen:
                    setattr(obj, nama, None)
                    dibuang.append(alias)

    periksa(ext, "")
    if dibuang:
        logger.warning(f"Nilai salinan contoh prompt dikosongkan: {dibuang}")
    return dibuang


def sanitize_sph_totals(ext: "SPHExtractionSchema") -> None:
    """
    Kosongkan PPN / Grand Total yang absurd dari LLM. JANGAN menghitung ulang.

    Versi sebelumnya mengganti nilai absurd dengan subtotal × tarif. Hasilnya angka yang
    tidak pernah tertulis di dokumen (SPH PT Vendor A: PPN 57.156.000 dan Grand Total
    576.756.000, padahal dokumen menulis 52.437.916 dan 533.218.400), dan karena angka
    karangan itu saling konsisten, validasi aritmetika meloloskannya dengan status `pass`.
    Nilai kosong justru jujur: ia muncul sebagai field hilang yang harus diisi PM.
    """
    subtotal = ext.subtotal
    if not subtotal or subtotal <= 0:
        return

    ppn = ext.ppn_nominal
    grand = ext.grand_total
    # PPN paling besar ~12% dari subtotal; Grand Total paling besar ~1.12× subtotal.
    ppn_absurd = ppn is not None and ppn > subtotal
    grand_absurd = grand is not None and grand > subtotal * 2
    if not (ppn_absurd or grand_absurd):
        return
    logger.warning(
        f"Sanity guard: PPN/Grand Total absurd (PPN={ppn}, Grand={grand}, "
        f"Subtotal={subtotal}) -> dikosongkan untuk ditinjau PM, tidak dihitung ulang."
    )
    if ppn_absurd:
        ext.ppn_nominal = None
    if grand_absurd:
        ext.grand_total = None


# Pembanding nama perusahaan. OCR dan LLM menulis nama perusahaan berbeda-beda (PT/PT., Tbk,
# "(Persero)"), dan klien utama muncul sebagai "Telkom" maupun "Telekomunikasi".
_ALIAS_PERUSAHAAN = {"telekomunikasi": "telkom"}
_KATA_UMUM_PERUSAHAAN = {"tbk", "persero", "perseroan", "perusahaan"}


def _kata_perusahaan(s: str | None) -> set[str]:
    kata = re.findall(r"[a-z0-9]{3,}", (s or "").lower())
    return {_ALIAS_PERUSAHAAN.get(k, k) for k in kata} - _KATA_UMUM_PERUSAHAAN


def perusahaan_sama(a: str | None, b: str | None) -> bool:
    """Nama perusahaan dianggap sama bila salah satunya kosong atau separuh katanya beririsan."""
    ka, kb = _kata_perusahaan(a), _kata_perusahaan(b)
    if not ka or not kb:
        return True
    return len(ka & kb) / min(len(ka), len(kb)) >= 0.5


# Pemasangan penandatangan per kolom. Kasus nyata (Nota Pesanan, kontrak layanan): tanda tangan
# dua kolom diratakan OCR menjadi satu urutan, lalu LLM memasang direktur penyedia sebagai wakil
# pemberi kerja dan sebaliknya, berstatus bukti_kuat. Bbox Docling masih menyimpan kolomnya.
# Nilai LLM tetap sumber utama: kolom dipakai untuk memastikan, mengganti wakil yang ternyata
# tertulis di kolom pihak lain, dan mengisi yang kosong. Bila kolom tidak meyakinkan (kepala
# kolom tergabung satu blok, tidak ada pasangan sejajar), tidak ada yang diubah.
_JABATAN_AWAL = re.compile(
    r"^(?:direktur|direktor|director|dirut|manager|manajer|mgr|sm|gm|vp|evp|svp|senior|kepala|kabag|"
    r"kasubag|ketua|sekretaris|pimpinan|presiden|komisaris|asman|staf|staff|officer|head|chief|"
    r"koordinator|ppk|rektor|dekan|wakil|plt|pgs|pjs)(?:$|[^a-z])",
    re.IGNORECASE,
)
_JABATAN_MENEMPEL = re.compile(r"^(?:SM|GM|VP|MGR)[A-Z][a-z]")  # "SMRegional" hasil OCR
_DERAU_TTD = {
    "METERAI",
    "METERA",
    "ETERAI",
    "TEMPEL",
    "EMPEL",
    "TEMRES",
    "METER",
    "SEPULUH",
    "SEPULU",
}
_ROMAWI = {"I", "II", "III", "IV", "V", "VI", "VII"}
_LABEL_PIHAK = (
    ("Pihak Pertama", re.compile(r"^\W*PIHAK\s+(?:PERTAMA|KESATU|KE-?\s*1)\W*$", re.I)),
    ("Pihak Kedua", re.compile(r"^\W*PIHAK\s+(?:KEDUA|KE-?\s*2)\W*$", re.I)),
)


def _awal_jabatan(token: str) -> bool:
    t = token.strip(".,:;()")
    return bool(_JABATAN_AWAL.match(t) or _JABATAN_MENEMPEL.match(t))


def _bersihkan_baris_ttd(teks: str) -> list[str]:
    """Token baris tanda tangan tanpa teks meterai, kode meterai, dan teks logo."""
    from app.parsers.text_cleaner import fix_common_ocr_typos

    teks = fix_common_ocr_typos(re.sub(r"[_*]+", " ", teks))
    token = [t for t in teks.split() if not _derau_meterai(t) and not re.search(r"\d", t)]
    huruf = [t for t in token if re.search(r"[A-Za-z]", t)]
    # Baris tanda tangan biasanya kapital; teks logo yang menempel tidak ("Be Tel - U").
    if huruf and sum(t.isupper() for t in huruf) / len(huruf) >= 0.6:
        token = [t for t in token if not re.search(r"[A-Za-z]", t) or t.isupper()]
    # Token pendek umumnya sisa logo/OCR, kecuali jabatan dua huruf ("SM", "GM") dan angka romawi.
    return [
        t for t in token if t == "&" or len(t.strip(".,-+")) > 2 or t in _ROMAWI or _awal_jabatan(t)
    ]


def _derau_meterai(token: str) -> bool:
    """Teks meterai dan variannya hasil OCR ("MMTERAI", "ETERAI", "VMETERAY")."""
    from difflib import SequenceMatcher

    t = re.sub(r"[^A-Z]", "", token.upper())
    return t in _DERAU_TTD or any(
        len(t) >= 5 and SequenceMatcher(None, t, k).ratio() >= 0.75 for k in ("METERAI", "TEMPEL")
    )


def _pisah_nama_jabatan(token: list[str]) -> tuple[str | None, str | None]:
    i = next((k for k, t in enumerate(token) if _awal_jabatan(t)), len(token))
    nama = [t for t in token[:i] if re.fullmatch(r"[A-Za-z.,'-]+", t)]
    nama = " ".join(nama).strip(" ,.") if 0 < len(nama) <= 5 else None
    jabatan = " ".join(token[i:]).strip(" ,.") or None
    return nama, jabatan


def _nama_sama(a: str | None, b: str | None) -> bool:
    from difflib import SequenceMatcher

    x, y = re.sub(r"[^A-Z]", "", (a or "").upper()), re.sub(r"[^A-Z]", "", (b or "").upper())
    if len(x) < 3 or len(y) < 3:
        return False
    return SequenceMatcher(None, x, y).ratio() >= 0.8 or (
        min(len(x), len(y)) >= 4 and (x in y or y in x)
    )


def _kepala_kolom(teks: str, perusahaan: dict[str, str | None]) -> str | None:
    for pihak, pola in _LABEL_PIHAK:
        if pola.match(teks):
            return pihak
    cocok = [
        p
        for p, nama in perusahaan.items()
        if _kata_perusahaan(nama) and _kata_perusahaan(teks) and perusahaan_sama(teks, nama)
    ]
    if len(cocok) != 1:
        return None  # tidak cocok, atau memuat kedua perusahaan sekaligus
    # Blok kepala kolom isinya nama perusahaan; kalimat yang kebetulan menyebutnya bukan kepala.
    lebih = len(_kata_perusahaan(teks) - _kata_perusahaan(perusahaan[cocok[0]]))
    return cocok[0] if lebih <= 4 else None


def _kepala_tergabung(ir: Any, perusahaan: dict[str, str | None]) -> bool:
    """
    Ada blok yang isinya nama kedua perusahaan sekaligus: kepala dua kolom tanda tangan yang
    dibaca OCR sebagai satu blok. Kolom siapa-di-bawah-siapa tidak bisa ditentukan, jadi wakil
    yang diisi LLM tidak bisa dicek perannya.
    """
    if not all(perusahaan.values()):
        return False
    for page in ir.pages:
        for b in page.blocks:
            if not b.bbox or len(b.text) > 200:
                continue
            if not all(perusahaan_sama(b.text, n) for n in perusahaan.values()):
                continue
            sisa = _kata_perusahaan(b.text)
            for n in perusahaan.values():
                sisa -= _kata_perusahaan(n)
            if len(sisa) <= 4:  # kalimat pembuka yang menyebut keduanya bukan kepala kolom
                return True
    return False


def _peran_tertulis(ir: Any, nama: str, milik: str | None, lain: str | None) -> bool:
    """Nama wakil tertulis satu blok dengan perusahaannya sendiri, tanpa perusahaan pihak lain
    ("PT X, dalam hal ini diwakili oleh Y"). Perannya tertulis, tidak perlu dari tata letak."""
    cari = re.sub(r"\s+", " ", nama).strip().lower()
    for page in ir.pages:
        for b in page.blocks:
            teks = re.sub(r"\s+", " ", b.text).lower()
            if cari in teks and perusahaan_sama(teks, milik) and not perusahaan_sama(teks, lain):
                return True
    return False


def _kolom_ttd(ir: Any, perusahaan: dict[str, str | None]) -> dict[str, list[tuple]]:
    """(nama, jabatan) penandatangan per pihak, dari setiap pasangan kepala kolom yang sejajar."""
    kolom: dict[str, list[tuple]] = {p: [] for p in perusahaan}
    for page in ir.pages:
        kepala = [
            (p, b) for b in page.blocks if b.bbox and (p := _kepala_kolom(b.text, perusahaan))
        ]
        for p1, b1 in kepala:
            for p2, b2 in kepala:
                kiri_kanan = b1.bbox[2] <= b2.bbox[0]
                if p1 == p2 or not kiri_kanan or abs(b1.bbox[1] - b2.bbox[1]) > 0.04:
                    continue
                batas = (b1.bbox[2] + b2.bbox[0]) / 2
                atas = max(b1.bbox[3], b2.bbox[3])
                for pihak, sisi in ((p1, "kiri"), (p2, "kanan")):
                    calon = [
                        b
                        for b in page.blocks
                        if b.bbox
                        and b not in (b1, b2)
                        and atas <= b.bbox[1] <= atas + 0.3
                        and (
                            b.bbox[2] <= batas + 0.02
                            if sisi == "kiri"
                            else b.bbox[0] >= batas - 0.02
                        )
                        and len(b.text) < 160
                    ]
                    nama_tunda = None
                    for b in sorted(calon, key=lambda b: b.bbox[1]):
                        # Daftar tembusan di bawah tanda tangan berisi jabatan orang lain.
                        if re.match(r"^\W*tembusan\b", b.text, re.I):
                            break
                        nama, jabatan = _pisah_nama_jabatan(_bersihkan_baris_ttd(b.text))
                        if nama:
                            kolom[pihak].append((nama, jabatan))
                            nama_tunda = None if jabatan else len(kolom[pihak]) - 1
                        elif jabatan and nama_tunda is not None:
                            kolom[pihak][nama_tunda] = (kolom[pihak][nama_tunda][0], jabatan)
                            nama_tunda = None
    return kolom


def _calon_terbaik(calon: list[tuple], ir: Any) -> tuple[str | None, str | None]:
    """Nama yang paling sering muncul (salinan tanda tangan bisa rusak di satu halaman), lalu
    yang paling lengkap. Jabatan diambil dari salinan lain bila kolom hanya memuat nama."""
    if not calon:
        return None, None

    def kunci(n: str) -> str:
        return re.sub(r"[^A-Z]", "", n.upper())

    skor = {
        nama: (
            sum(_nama_sama(nama, n) for n, _ in calon),
            sum(kunci(nama) == kunci(n) for n, _ in calon),
            len(nama.split()),
            nama.isascii(),
        )
        for nama, _ in calon
    }
    nama = _gabung_ejaan(max(skor, key=skor.get), [n for n, _ in calon])
    jabatan = [j for n, j in calon if j and _nama_sama(n, nama)]
    if not jabatan:
        for page in ir.pages:
            for b in page.blocks:
                n, j = _pisah_nama_jabatan(_bersihkan_baris_ttd(b.text))
                if j and n and _nama_sama(n, nama) and len(b.text) < 160:
                    jabatan.append(j)
    if not jabatan:
        return nama, None
    return nama, max(jabatan, key=lambda j: (jabatan.count(j), j.isascii(), len(j)))


def _gabung_ejaan(nama: str, semua: list[str]) -> str:
    """Satukan ejaan dari beberapa salinan tanda tangan, kata demi kata. Kasus nyata: satu
    salinan terbaca "ANDI WIJ'AYA PRASETYO", salinan lain "ANDI WJAYA PRASETYO". Per
    posisi dipilih kata dengan huruf terbanyak (OCR lebih sering menghilangkan huruf daripada
    menambah), lalu yang tanpa tanda baca. Apostrof dibuang bila salinan lain tidak memuatnya."""
    kata = nama.split()
    sejenis = [n.split() for n in semua if _nama_sama(n, nama) and len(n.split()) == len(kata)]
    hasil = []
    for i, k in enumerate(kata):
        pilihan = [s[i] for s in sejenis] or [k]
        w = max(pilihan, key=lambda w: (len(re.sub(r"[^A-Za-z]", "", w)), w.isalpha()))
        if "'" in w and any("'" not in p for p in pilihan):
            w = w.replace("'", "")
        hasil.append(w)
    return " ".join(hasil)


_HORMAT_KAMI = re.compile(r"hormat\s+kami", re.IGNORECASE)


def _penandatangan_tunggal(ir: Any, perusahaan: dict[str, str | None]) -> dict[str, list[tuple]]:
    """Surat yang hanya ditandatangani penerbitnya: "Hormat Kami, a.n <perusahaan> / <jabatan>"
    lalu nama di bawahnya (kasus nyata: SPK dari BUT ke vendor). Nama diambil sebagai bagian akhir
    terpanjang baris itu yang juga muncul di tempat lain dokumen, karena teks logo sering
    menempel ("Be Tel / Andi / Wijaya") dan baris itu tidak kapital sehingga filter logo
    tidak jalan."""
    seluruh = " ".join(re.sub(r"\s+", " ", b.text) for pg in ir.pages for b in pg.blocks).lower()
    hasil: dict[str, list[tuple]] = {p: [] for p in perusahaan}
    for page in ir.pages:
        for i, b in enumerate(page.blocks):
            m = _HORMAT_KAMI.search(b.text)
            if not b.bbox or not m:
                continue
            baris = [x.strip() for x in b.text[m.end() :].splitlines() if x.strip()]
            pihak = [
                (k, p)
                for k, x in enumerate(baris)
                for p, nama in perusahaan.items()
                if _kata_perusahaan(nama)
                and len(_kata_perusahaan(nama) & _kata_perusahaan(x)) / len(_kata_perusahaan(nama))
                >= 0.5
            ]
            if len({p for _, p in pihak}) != 1:
                continue
            k, p = pihak[0]
            jabatan = " ".join(baris[k + 1 :]).strip(" ,.") or None
            if jabatan and not _awal_jabatan(jabatan.split()[0]):
                jabatan = None
            for c in page.blocks[i + 1 : i + 4]:
                if not c.bbox or abs(c.bbox[0] - b.bbox[0]) > 0.1 or c.bbox[1] > b.bbox[3] + 0.25:
                    continue
                kata = [
                    t for t in re.findall(r"[A-Za-z][A-Za-z.'-]*", c.text) if not _awal_jabatan(t)
                ]
                nama = next(
                    (
                        " ".join(kata[j:])
                        for j in range(len(kata))
                        if len(kata) - j <= 5 and seluruh.count(" ".join(kata[j:]).lower()) >= 2
                    ),
                    None,
                )
                if nama:
                    hasil[p].append((nama, jabatan))
                    break
    return hasil


def pasangkan_penandatangan(data: dict[str, Any], ir: Any) -> list[dict[str, Any]]:
    """
    Cocokkan wakil & jabatan kedua pihak dengan kolom tanda tangan; ubah `data` di tempat.

    Kembali: daftar perubahan {pihak, jenis, lama, baru, perusahaan} untuk catatan PM. Jenis
    "diganti"/"diisi" bila kedua kolom ditemukan; "tak_pasti" bila kepala kolom tergabung
    sehingga wakil isian LLM tidak bisa dicek (nilainya tidak diubah).
    """
    pihak = {p: data.get(p) or {} for p in ("Pihak Pertama", "Pihak Kedua")}
    perusahaan = {p: d.get("Nama Perusahaan") for p, d in pihak.items()}
    kolom = _kolom_ttd(ir, perusahaan)
    perubahan: list[dict[str, Any]] = []
    if not all(kolom.values()):
        # Tanpa pasangan kolom hanya boleh mengisi yang kosong; tidak ada pembanding untuk
        # menyatakan nilai LLM tertukar.
        for p, calon in _penandatangan_tunggal(ir, perusahaan).items():
            d = pihak[p]
            nama, jabatan = _calon_terbaik(calon, ir)
            if nama and not d.get("Nama Representative"):
                d["Nama Representative"] = nama
                d["Jabatan"] = d.get("Jabatan") or jabatan
                perubahan.append(
                    {
                        "pihak": p,
                        "jenis": "diisi",
                        "lama": None,
                        "baru": nama,
                        "perusahaan": perusahaan[p],
                    }
                )
        # Kasus nyata (nota pesanan, scan): kepala dua kolom terbaca satu blok, lalu LLM
        # memasang penandatangan Pihak Pertama sebagai wakil Pihak Kedua, dan grounding
        # memberinya bukti_kuat karena namanya memang ada di dokumen.
        if _kepala_tergabung(ir, perusahaan):
            diisi = {c["pihak"] for c in perubahan}
            lain = {"Pihak Pertama": "Pihak Kedua", "Pihak Kedua": "Pihak Pertama"}
            for p, d in pihak.items():
                rep = d.get("Nama Representative")
                if (
                    rep
                    and p not in diisi
                    and not _peran_tertulis(ir, rep, perusahaan[p], perusahaan[lain[p]])
                ):
                    perubahan.append(
                        {
                            "pihak": p,
                            "jenis": "tak_pasti",
                            "lama": rep,
                            "baru": rep,
                            "perusahaan": perusahaan[p],
                        }
                    )
        return perubahan
    lain = {"Pihak Pertama": "Pihak Kedua", "Pihak Kedua": "Pihak Pertama"}
    for p, d in pihak.items():
        rep = d.get("Nama Representative")
        di_sendiri = rep and any(_nama_sama(rep, n) for n, _ in kolom[p])
        di_lain = rep and any(_nama_sama(rep, n) for n, _ in kolom[lain[p]])
        nama, jabatan = _calon_terbaik(kolom[p], ir)
        if not nama or di_sendiri:
            if di_sendiri and not d.get("Jabatan") and jabatan:
                d["Jabatan"] = jabatan
            continue
        if rep and not di_lain:
            continue  # nama LLM tidak ada di kolom mana pun: biarkan, jangan menebak
        jenis = "diganti" if rep else "diisi"
        perubahan.append(
            {"pihak": p, "jenis": jenis, "lama": rep, "baru": nama, "perusahaan": perusahaan[p]}
        )
        d["Nama Representative"], d["Jabatan"] = (
            nama,
            jabatan or (None if rep else d.get("Jabatan")),
        )
    if perubahan:
        logger.info(
            "Penandatangan dari kolom tanda tangan: "
            + "; ".join(f"{c['pihak']} {c['jenis']}" for c in perubahan)
        )
    return perubahan
