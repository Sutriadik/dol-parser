"""
Open ADE — Rekonsiliasi deterministik hasil LLM dengan teks dokumen.

Fungsi murni yang mencocokkan keluaran LLM dengan sumber yang lebih bisa dipercaya: tabel
yang terbaca deterministik, nominal yang ditulis angka sekaligus terbilang, dan aritmetika
total SPH. Dipindah dari ollama_client.py (isi tidak berubah).
"""

import re
from typing import Any

from pydantic import BaseModel

from app.extractors.deterministic.numbers import (
    amounts_equal,
    confirmed_amounts,
    numbers_in_text,
    terbilang_to_number,
)
from app.logger import logger
from app.parsers.table_extractor import clean_desc_and_extract_item_no
from app.schemas.bast import BASTItemDetail
from app.schemas.sph import SPHExtractionSchema

SUMMARY_ROW = re.compile(
    r"^(?:sub\s*total|grand\s*total|total\s*[a-z0-9+]*|jumlah\s*total|total\s*keseluruhan)\b",
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

    def matches_reference(amount: float) -> bool:
        return any(ref and amounts_equal(amount, ref, 1000.0) for ref in reference_amounts)

    def jumlah(items: list[dict[str, Any]]) -> float:
        return sum(float(t.get(total_alias) or 0) for t in items)

    if table_groups and len(table_groups) > 1 and not matches_reference(jumlah(table_items)):
        # Bundel kontrak (BA negosiasi + penawaran + nota pesanan) bisa memuat tabel harga yang sama
        # beberapa kali; menggabungkan semuanya menggandakan item. Barisnya tidak selalu identik,
        # jadi yang dipilih SATU tabel utuh yang jumlahnya sama dengan nominal tertulis di dokumen.
        # Rujukan itu isian LLM, maka disaring dulu: tanpa rujukan tertulis, semua tabel tetap
        # dipakai.
        tertulis = [
            r
            for r in reference_amounts
            if r and markdown_text and nominal_tertulis(r, markdown_text)
        ]
        salinan = [
            g for g in table_groups if any(amounts_equal(jumlah(g), r, 1000.0) for r in tertulis)
        ]
        if salinan:
            table_items = max(salinan, key=len)
            logger.info(
                f"{len(table_groups)} tabel harga, gabungannya tidak cocok subtotal/total; "
                f"memakai satu tabel ({len(table_items)} item) yang jumlahnya sama dengan "
                "nominal tertulis di dokumen"
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
