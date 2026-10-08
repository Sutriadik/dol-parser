"""
Open ADE — Context Analyzer (deterministic pre-processor sebelum LLM).

1. Section Detection — mendeteksi pasal-pasal dokumen kontrak/SPH
2. Key Entity Pre-extraction — regex berbasis konteks (satu kandidat terbaik per field)
3. Context Enrichment — annotation [SECTION:] / [KEY:] untuk membantu LLM kecil

Perbaikan dari versi sebelumnya:
- Pola lama "No.\\s*(\\d+)" menangkap "No. 1" di alamat sebagai nomor rekening, "a.n PT" menjadi
  nama rekening "PT", dan tanggal pertama di dokumen (bisa tanggal akta 1991) menjadi tanggal
  pembuatan dokumen. Hint salah itu disisipkan ke prompt sebagai [KEY: ...] dan menyesatkan LLM.
  Pola bank sekarang hanya dicari di sekitar kata "rekening", tanggal dokumen hanya setelah
  "Dibuat di".
- Normalisasi angka tidak lagi merusak NPWP / nomor rekening.
- Hasil analisis di-cache: enrich_markdown() dan get_entity_hints() tidak menghitung ulang.
- Keyword alamat yang spesifik satu dokumen (nama jalan tertentu) dihapus.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any

from app.extractors.deterministic.dates import find_dates
from app.extractors.deterministic.numbers import normalize_id_money_in_text
from app.logger import logger


def _di_baris_tabel(text: str, m: re.Match) -> bool:
    """Kecocokan regex jatuh di baris tabel markdown ("| ... |")."""
    awal = text.rfind("\n", 0, m.start()) + 1
    return text[awal : m.start()].lstrip().startswith("|")


@dataclass
class DocumentSection:
    title: str
    content: str
    section_type: str
    start_line: int
    end_line: int


@dataclass
class PreExtractedEntity:
    field_name: str
    value: str
    source_line: str
    line_index: int = 0
    confidence: float = 0.8


@dataclass
class ContextAnalysis:
    normalized_text: str
    sections: list[DocumentSection] = field(default_factory=list)
    entities: list[PreExtractedEntity] = field(default_factory=list)
    parties: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def hints(self) -> dict[str, Any]:
        result: dict[str, Any] = {e.field_name: e.value for e in self.entities}
        if self.parties.get("pihak_pertama"):
            result["Pihak Pertama"] = self.parties["pihak_pertama"]
        if self.parties.get("pihak_kedua"):
            result["Pihak Kedua"] = self.parties["pihak_kedua"]
        return result


_ADDRESS_KEYWORDS = [
    "jl.",
    "jl ",
    "ji.",
    "jln",
    "jalan",
    "kampus",
    "gedung",
    "komplek",
    "kompleks",
    "graha",
    "lantai",
    "blok",
    "rt/rw",
    "rt.",
    "kel.",
    "kelurahan",
    "kec.",
    "kecamatan",
    "kota ",
    "kabupaten",
]
_BANK_STOP = r"(?!(?i:cabang|kcp|kc|unit|no|nomor|rekening|dengan|sebesar|atas)\b|a\.n)"

# "Pihak Kesatu" adalah sinonim resmi "Pihak Pertama" yang umum dipakai di dokumen
# Perjanjian Kerja Sama (PKS). Beberapa dokumen juga memakai "Pihak Ke-1"/"Pihak Ke-2".
_FIRST_PARTY_LABEL = r"PIHAK\s+(?:PERTAMA|KESATU|KE-?\s*1)"
_SECOND_PARTY_LABEL = r"PIHAK\s+(?:KEDUA|KE-?\s*2)"


class ContextAnalyzer:
    SECTION_PATTERNS = [
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*LINGKUP\s+PEKERJAAN", "scope"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*(?:NILAI\s+KONTRAK|HARGA)", "price"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*(?:WAKTU\s+PELAKSANAAN|JANGKA\s+WAKTU)", "duration"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*(?:CARA|SYARAT|TATA\s+CARA)\s+PEMBAYARAN", "payment"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*GARANSI", "guarantee"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*(?:SANKSI|DENDA)", "penalty"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*(?:SERAH\s+TERIMA|BERITA\s+ACARA)", "handover"),
        (r"(?:^|\n)\s*(?:pasal\s+)?\d+\.?\s*LAIN[- ]?LAIN", "closing"),
        (r"(?:^|\n)\s*Lampiran\s*:", "attachment"),
        (r"(?:^|\n)\s*[#*\s]*SURAT\s+PERINTAH\s+KERJA", "preamble"),
        (r"(?:^|\n)\s*[#*\s]*SURAT\s+PENAWARAN", "preamble"),
        (r"(?:^|\n)\s*Demikian\s+Surat", "closing"),
    ]

    def __init__(self) -> None:
        self._cache_key: int | None = None
        self._cache_value: ContextAnalysis | None = None

    # ------------------------------------------------------------------ analysis
    def analyze(self, markdown_text: str) -> ContextAnalysis:
        key = hash(markdown_text)
        if self._cache_key == key and self._cache_value is not None:
            return self._cache_value
        normalized = self.normalize_indonesian_numbers(markdown_text)
        analysis = ContextAnalysis(
            normalized_text=normalized,
            sections=self.detect_sections(normalized),
            entities=self.pre_extract_entities(normalized),
            parties=self.extract_parties_from_preamble(normalized),
        )
        self._cache_key, self._cache_value = key, analysis
        return analysis

    def detect_sections(self, markdown_text: str) -> list[DocumentSection]:
        lines = markdown_text.split("\n")
        sections = []
        for pattern, section_type in self.SECTION_PATTERNS:
            for match in re.finditer(pattern, markdown_text, re.IGNORECASE):
                line_num = markdown_text[
                    : match.start() + (1 if match.group(0).startswith("\n") else 0)
                ].count("\n")
                sections.append(
                    DocumentSection(match.group(0).strip(), "", section_type, line_num, line_num)
                )
        sections.sort(key=lambda s: s.start_line)
        for i, section in enumerate(sections):
            end = sections[i + 1].start_line if i + 1 < len(sections) else len(lines)
            section.end_line = end
            section.content = "\n".join(lines[section.start_line : end])
        return sections

    @staticmethod
    def _entity(
        text: str, field_name: str, value: str, pos: int, confidence: float = 0.75
    ) -> PreExtractedEntity | None:
        value = re.sub(r"^[\s:]+|[\s:,.;]+$", "", value or "").strip()
        if len(value) < 2:
            return None
        line_start = text.rfind("\n", 0, pos) + 1
        line_end = text.find("\n", pos)
        line_end = len(text) if line_end == -1 else line_end
        return PreExtractedEntity(
            field_name,
            value,
            text[line_start:line_end].strip()[:160],
            text.count("\n", 0, pos),
            confidence,
        )

    def pre_extract_entities(self, markdown_text: str) -> list[PreExtractedEntity]:
        """Satu kandidat terbaik per field; hanya pola yang punya konteks kuat."""
        text = markdown_text
        found: dict[str, PreExtractedEntity] = {}

        def put(entity: PreExtractedEntity | None) -> None:
            if entity and entity.field_name not in found:
                found[entity.field_name] = entity

        # --- Bank: hanya di sekitar kata "rekening"
        for anchor in re.finditer(r"rekening", text, re.IGNORECASE):
            w_start, w_end = max(0, anchor.start() - 120), min(len(text), anchor.end() + 260)
            window = text[w_start:w_end]
            # Nama bank harus diawali huruf kapital ("bank ke BANK MANDIRI" -> "Bank MANDIRI", bukan
            # "Bank ke").
            m = re.search(
                rf"\b(?i:bank)[ \t]+((?:{_BANK_STOP}[A-Z][A-Za-z]*[ \t]*){{1,4}})", window
            )
            if m:
                put(
                    self._entity(
                        text, "Nama Bank", "Bank " + m.group(1).strip(), w_start + m.start()
                    )
                )
            m = re.search(
                rf"\b(?i:cabang|kcp|kc)[ \t]+((?:{_BANK_STOP}[A-Za-z0-9]+[ \t]*){{1,5}})", window
            )
            if m:
                put(self._entity(text, "Lokasi Cabang Bank", m.group(1), w_start + m.start()))
            m = re.search(
                r"\bno(?:mor)?\.?\s*(?:rek(?:ening)?\.?)?\s*:?\s*(\d[\d.\-\s]{4,}\d)",
                window,
                re.IGNORECASE,
            )
            if m and len(re.sub(r"\D", "", m.group(1))) >= 6:
                put(self._entity(text, "Nomor Rekening Bank", m.group(1), w_start + m.start()))
            m = re.search(
                r"(?:\ba\.\s?n\.?|\batas\s+nama)\s*:?\s*([A-Za-z][A-Za-z0-9.&\' ]{2,80}?)"
                r"(?=\s*(?:[,;\n(]|$)|\s+(?:no|nomor|cabang|dengan|pada|di)\b)",
                window,
                re.IGNORECASE,
            )
            if (
                m
                and len(re.sub(r"\b(?:pt|cv)\b\.?", "", m.group(1), flags=re.IGNORECASE).strip())
                >= 4
            ):
                put(self._entity(text, "Nama Rekening Bank", m.group(1), w_start + m.start()))

        # --- Nomor Kontrak / SPK / Dokumen
        contract_patterns = [
            r"(?:Nomor\s+Kontrak(?:\s+Kerja)?|Nomor\s+SPK|No\.?\s*SPK|Nomor\s+PKS|No\.?\s*PKS)\s*:?\s*([A-Za-z0-9.\-_/]{4,}(?:[ \t]*/[ \t]*[A-Za-z0-9.\-_/]+)*)",  # noqa: E501 (pola regex dibiarkan utuh)
            r"(?:^|\n)\s*(?:##\s*)?(?:SURAT\s+PERINTAH\s+KERJA|KONTRAK\s+LAYANAN|PERJANJIAN\s+KERJA\s+SAMA)[\s\S]{0,160}?(?:Nomor|No\.?)\s*:?\s*([A-Za-z0-9.\-_/]{4,}(?:[ \t]*/[ \t]*[A-Za-z0-9.\-_/]+)*)",  # noqa: E501 (pola regex dibiarkan utuh)
            r"(?:^|\n)\s*(?:##\s*)?Nomor\s*:?\s*([A-Za-z0-9.\-_/]{5,}(?:[ \t]*/[ \t]*[A-Za-z0-9.\-_/]+)*)",  # noqa: E501 (pola regex dibiarkan utuh)
        ]
        all_contract_nos = []
        for cp in contract_patterns:
            for m_cp in re.finditer(cp, text, re.IGNORECASE):
                val = m_cp.group(1).strip()
                val = re.sub(r"\s+", "", val)
                # Valid contract numbers must contain at least one digit and not be reserved noise
                # words
                if (
                    any(c.isdigit() for c in val)
                    and len(val) >= 5
                    and not val.lower().startswith(("rekening", "telepon", "npwp", "akta", "pasal"))
                    and val not in all_contract_nos
                ):
                    all_contract_nos.append(val)

        if all_contract_nos:
            put(self._entity(text, "Nomor Kontrak Kerja", all_contract_nos[0], 0, 0.95))
            if len(all_contract_nos) > 1:
                put(self._entity(text, "Nomor Kontrak Internal", all_contract_nos[1], 0, 0.90))

        # --- Tanggal Negosiasi: mencari konsiderans negosiasi/kesepakatan harga
        m_neg = re.search(
            r"(?:negosiasi\s+harga|kesepakatan\s+harga|klarifikasi\s+dan\s+negosiasi|berita\s+acara\s+negosiasi)[\s\S]{0,40}?"
            r"(?:pada\s+)?(?:tanggal|tgl\.?)\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if not m_neg:
            m_neg = re.search(
                r"negosiasi\s+(?:harga\s+)?(?:pada\s+)?(?:tanggal\s+)?(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
                text,
                re.IGNORECASE,
            )
        if m_neg:
            put(self._entity(text, "Tanggal Negosiasi", m_neg.group(1), m_neg.start(), 0.85))

        # --- Nama Pekerjaan: mendeteksi judul/lingkup pengadaan dari frasa standar dokumen
        job_patterns = [
            r"Nama\s+Pekerjaan\s*:?\s*([^\n]+)",
            r"memberi\s+perintah\s+kerja\s+([\s\S]+?)\s+(?:kepada\s*:|dengan\s+uraian|\s+sesuai)",
            # Berhenti di label kop berikutnya ("Lampiran :", "Kepada Yth", "Dengan hormat")
            # dan di "tanggal <angka>" pada rujukan surat. Isian yang diawali label kop
            # berarti OCR menumpuk kolom label ("Perihal\nLampiran ..."); tangkapan itu
            # dilewati dan pencarian lanjut ke "perihal" berikutnya.
            r"\b(?:tentang|perihal|mengenai)(?:\s*:)?\s+(?!(?:lampiran|kepada|yth)\b)([\s\S]+?)"
            r"(?:\s+maka\s+kami|\s+antara|\s+nomor|\s+tanggal\s+\d|\s+(?:lampiran|kepada|yth)\s*:"
            r"|\n\s*(?:lampiran|kepada|yth|dengan\s+hormat)\b|\n\n|$)",
            r"Jumlah\s+harga\s+untuk\s+([\s\S]+?)\s+sebesar\s+Rp",
            r"(?:^|\n)\s*Lampiran\s*(?:SPK)?\s*:\s*([^\n]+)",
            r"##\s*1\.\s*LINGKUP\s+PEKERJAAN[\s\S]*?(?:memberi\s+perintah\s+kerja|melaksanakan|pekerjaan)\s+([\s\S]+?)\s+(?:dengan|sesuai|yang\s+diminta)",
        ]
        for jp in job_patterns:
            # Kecocokan di baris tabel dilewati: "| No | Nama Pekerjaan | Pelanggan | Qty |" adalah
            # judul kolom, bukan label "Nama Pekerjaan: ...". Dulu (Nota Pesanan, 8 Okt 2026) pola
            # pertama menangkap sisa judul kolom sebagai nama pekerjaan, hint-nya disisipkan ke
            # dalam tabel, dan LLM menyalin isi baris tabel ("Pelanggan: ..., Masa Layanan: 8").
            m_job = next(
                (m for m in re.finditer(jp, text, re.IGNORECASE) if not _di_baris_tabel(text, m)),
                None,
            )
            if m_job:
                raw_job = re.sub(r"[\r\n\t]+", " ", m_job.group(1)).strip()
                raw_job = re.sub(
                    r"\s+(?:kepada|dengan|sesuai|sebesar|maka|antara)\s*:?$",
                    "",
                    raw_job,
                    flags=re.IGNORECASE,
                )
                raw_job = re.sub(r"^[^\w]+|[^\w)]+$", "", raw_job).strip()
                if len(raw_job) >= 10 and not raw_job.lower().startswith(
                    ("pihak", "pasal", "surat")
                ):
                    put(self._entity(text, "Nama Pekerjaan", raw_job, m_job.start(), 0.85))
                    break

        # "Dibuat di : Bandung" — nama kota berhuruf kapital, satu baris ("dibuat di hadapan
        # Notaris" tidak ikut).
        closings = list(
            re.finditer(
                r"(?i:dibuat\s+di|bertempat\s+di)[ \t]*:?[ \t]*(?:\n\s*:?[ \t]*)?([A-Z][A-Za-z]+(?:[ \t]+[A-Z][a-z]+)?)\b",  # noqa: E501 (pola regex dibiarkan utuh)
                text,
            )
        )
        if closings:
            last = closings[-1]
            put(self._entity(text, "Lokasi", last.group(1), last.start(), 0.85))
            tail = text[last.end() : last.end() + 200]
            date_m = re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2}", tail)
            if date_m and find_dates(date_m.group(0)):
                put(
                    self._entity(
                        text,
                        "Tanggal Pembuatan Dokumen",
                        date_m.group(0),
                        last.end() + date_m.start(),
                        0.85,
                    )
                )

        m = re.search(
            r"(?:jangka\s+waktu|akses)\s+selama\s+(\d{1,2}\s+\w+\s+\d{4}\s*[-–s/d]+\s*\d{1,2}\s+\w+\s+\d{4})",
            text,
            re.IGNORECASE,
        )
        if m:
            put(self._entity(text, "Jangka Waktu", m.group(1), m.start()))
        m = re.search(
            r"(?:lama\s+pekerjaan|durasi|jangka\s+waktu\s+pelaksanaan)\s+(?:selama\s+)?(\d+\s*\(?[a-z\s]*\)?\s*hari\s+kalender|\d+\s*\(?[a-z\s]*\)?\s*bulan)",
            text,
            re.IGNORECASE,
        )
        if m:
            put(self._entity(text, "Durasi Kerja", m.group(1), m.start()))
        m = re.search(
            r"(?:denda|sanksi)\s+(?:keterlambatan\s+)?sebesar\s+(\d+\s*/\s*\d+\s*(?:\([^)]{1,30}\))?|\d+(?:[.,]\d+)?\s*‰|\d+(?:[.,]\d+)?\s*%)",
            text,
            re.IGNORECASE,
        )
        if m:
            put(self._entity(text, "Persentase Sanksi/Penalti", m.group(1), m.start()))

        return list(found.values())

    # ------------------------------------------------------------------ parties
    @staticmethod
    def extract_parties_from_preamble(markdown_text: str) -> dict[str, dict[str, str]]:
        parties: dict[str, dict[str, str]] = {"pihak_pertama": {}, "pihak_kedua": {}}
        text = html.unescape(markdown_text).replace("**", "")

        if re.search(_FIRST_PARTY_LABEL, text, re.IGNORECASE) and re.search(
            _SECOND_PARTY_LABEL, text, re.IGNORECASE
        ):
            p1_split = re.split(
                rf'selanjutnya\s+disebut\s*(?:sebagai\s*)?["\']?{_FIRST_PARTY_LABEL}["\']?',
                text,
                maxsplit=1,
                flags=re.IGNORECASE,
            )
            if len(p1_split) > 1:
                p1_block = p1_split[0]
                lowered = p1_block.lower()
                if "bertanda tangan" in lowered:
                    p1_block = p1_block[lowered.rfind("bertanda tangan") :]
                else:
                    p1_block = p1_block[-1200:]
                p2_split = re.split(
                    rf'selanjutnya\s+disebut\s*(?:sebagai\s*)?["\']?{_SECOND_PARTY_LABEL}["\']?',
                    p1_split[1],
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )
                p2_block = p2_split[0] if len(p2_split) > 1 else ""
                parties["pihak_pertama"] = ContextAnalyzer._parse_labeled_block(p1_block)
                parties["pihak_kedua"] = ContextAnalyzer._parse_labeled_block(p2_block)

        elif re.search(r"(?:1\.|\bI\.)\s*(?:PERUSAHAAN|PT\s+)", text, re.IGNORECASE) or re.search(
            r'selanjutnya disebut\s*["\']TELKOM', text, re.IGNORECASE
        ):
            # Penanda butir "1."/"2." harus berdiri sendiri. Tanpa batas ini "1." di dalam NPWP
            # semacam "01.234.567.8-901.000" dianggap awal blok pihak pertama (terjadi di kontrak
            # eval setelah OCR tidak lagi membuang baris akta, 8 Okt 2026): nama perusahaan
            # tercemar "... Tbk Nomor <akta> tanggal <tgl>", alamat terpotong, dan LLM
            # menyalin hint itu ke hasil.
            p1_m = re.search(
                r"(?:antara pihak-pihak:?[\s\n]*)?(?:(?<![\w.])1\.|\bI\.)\s*([\s\S]*?)(?=(?:\n\s*[-–•*]?\s*(?:2\.|\bII\.)|\n\s*II\.|\n\s*2\.))",  # noqa: E501 (pola regex dibiarkan utuh)
                text,
                re.IGNORECASE,
            )
            p2_m = re.search(
                r"(?:[-–•*]?\s*(?:(?<![\w.])2\.|\bII\.)|\bII\.)\s*([\s\S]*?)(?=(?:Selanjutnya dalam Kontrak|Para Pihak|Dengan terlebih dahulu|MENERANGKAN))",  # noqa: E501 (pola regex dibiarkan utuh)
                text,
                re.IGNORECASE,
            )
            if p1_m:
                parties["pihak_pertama"] = ContextAnalyzer._parse_kontrak_block(p1_m.group(1))
            if p2_m:
                parties["pihak_kedua"] = ContextAnalyzer._parse_kontrak_block(p2_m.group(1))
        return parties

    @staticmethod
    def _parse_labeled_block(block: str) -> dict[str, str]:
        """Blok 'Nama : X / Jabatan : Y / Alamat : Z ... mewakili secara sah : PERUSAHAAN'."""
        info: dict[str, str] = {}
        comp = re.search(
            r"mewakili\s+(?:secara\s+)?(?:sah\s*)?:?\s*([^,\n]+?)(?:\s*,|\s*$|\s+selanjutnya)",
            block,
            re.IGNORECASE,
        )
        if comp:
            info["nama_perusahaan"] = comp.group(1).strip(" :")

        npwp_m = re.search(r"NPWP\s*:?\s*([\d.\-]+)", block, re.IGNORECASE)
        if npwp_m:
            info["npwp"] = npwp_m.group(1).strip()

        lines = [b.strip() for b in block.split("\n") if b.strip()]
        label_map = {"nama": "nama_representative", "jabatan": "jabatan", "alamat": "alamat"}
        current = None
        for line in lines:
            labeled = re.match(r"^(nama|jabatan|alamat)\s*:\s*(.*)$", line, re.IGNORECASE)
            if labeled:
                current = label_map[labeled.group(1).lower()]
                if labeled.group(2).strip():
                    info[current] = labeled.group(2).strip()
                continue
            if line.lower().startswith(("yang dalam hal", "mewakili")):
                current = None
                continue
            if current == "alamat" and any(kw in line.lower() for kw in _ADDRESS_KEYWORDS + [","]):
                info["alamat"] = f"{info.get('alamat', '')} {line.lstrip(': ')}".strip()

        # Format OCR lama: nilai diawali ':' tanpa label di baris yang sama
        if "nama_representative" not in info:
            vals = [re.sub(r"^:\s*", "", b) for b in lines if b.startswith(":")]
            non_addr = [
                v
                for v in vals
                if not any(kw in v.lower() for kw in _ADDRESS_KEYWORDS + ["mewakili"])
            ]
            if non_addr:
                info["nama_representative"] = non_addr[0]
            if len(non_addr) >= 2 and "jabatan" not in info:
                info["jabatan"] = non_addr[1]
            if "alamat" not in info:
                addr = [v for v in vals if any(kw in v.lower() for kw in _ADDRESS_KEYWORDS)]
                if addr:
                    info["alamat"] = ", ".join(addr)
        return info

    @staticmethod
    def _parse_kontrak_block(block_text: str) -> dict[str, str]:
        info: dict[str, str] = {}
        if not block_text:
            return info
        comp_m = re.search(
            r"^([A-Z0-9\s().,]+?)(?:,\s*NPWP|,\s*sebuah|,\s*adalah|,\s*suatu|\s+NPWP:)",
            block_text.strip(),
            re.IGNORECASE,
        )
        if comp_m:
            info["nama_perusahaan"] = comp_m.group(1).strip()
        else:
            c_m = re.search(
                r"((?:PERUSAHAAN\s+PERSEROAN\s+\(PERSERO\)\s+)?PT\.?\s+[A-Za-z0-9\s.]+?)(?:,|\s+NPWP)",
                block_text,
            )
            if c_m:
                info["nama_perusahaan"] = c_m.group(1).strip()

        npwp_m = re.search(r"NPWP\s*:?\s*([\d.\-]+)", block_text, re.IGNORECASE)
        if npwp_m:
            info["npwp"] = npwp_m.group(1).strip()

        rep_m = re.search(
            r"diwakili\s+(?:secara\s+)?sah\s+oleh\s+([A-Za-z\s.,]+?),\s*Jabatan\s+([A-Za-z0-9\s.,&/\-]+?)(?:,|\s+selanjutnya|\.|$)",
            block_text,
            re.IGNORECASE,
        )
        if rep_m:
            info["nama_representative"] = rep_m.group(1).strip()
            info["jabatan"] = rep_m.group(2).strip()
        addr_m = re.search(
            r"(?:berkedudukan\s+di|berkantor\s+di|beralamat\s+di)\s+((?:Jalan|Jl\.|JI\.)\s+[\w\s.,–-]+?)(?=\s*,\s*dalam\s+perbuatan|\s+dalam\s+perbuatan|\s*$)",
            block_text,
            re.IGNORECASE,
        )
        if addr_m:
            info["alamat"] = addr_m.group(1).strip()
        else:
            j_m = re.search(
                r"(?:Jalan|Jl\.|JI\.)\s+[A-Za-z0-9\s.,–-]+?(?=\s*,\s*dalam\s+perbuatan|\s*,\s*yang|\.\s|$)",
                block_text,
                re.IGNORECASE,
            )
            if j_m:
                info["alamat"] = j_m.group(0).strip()
        return info

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def normalize_indonesian_numbers(text: str) -> str:
        """'Rp 13.500.000,-' -> 'Rp 13500000' tanpa merusak NPWP / nomor rekening."""
        return normalize_id_money_in_text(text)

    def enrich_markdown(self, markdown_text: str) -> str:
        analysis = self.analyze(markdown_text)
        text = analysis.normalized_text
        if (
            not analysis.sections
            and not analysis.entities
            and not analysis.parties.get("pihak_pertama")
        ):
            return text

        logger.info(
            f"🧠 Context Analyzer: {len(analysis.sections)} sections, {len(analysis.entities)} "
            "pre-extracted entities"
        )
        lines = text.split("\n")
        inserts = [
            (s.start_line, f"[SECTION: {s.section_type.upper()}]") for s in analysis.sections
        ]
        inserts += [
            (e.line_index + 1, f"[KEY: {e.field_name} = {e.value}]") for e in analysis.entities
        ]
        for line_num, marker in sorted(inserts, key=lambda x: x[0], reverse=True):
            lines.insert(min(line_num, len(lines)), marker)

        party_notes = []
        for key, label in (
            ("pihak_pertama", "Pihak Pertama (Pemberi Kerja)"),
            ("pihak_kedua", "Pihak Kedua (Penyedia)"),
        ):
            p = analysis.parties.get(key) or {}
            if p.get("nama_perusahaan"):
                party_notes.append(
                    f"[KEY: {label} = {p.get('nama_perusahaan')}, Representative: "
                    f"{p.get('nama_representative')}, "
                    f"Jabatan: {p.get('jabatan')}, Alamat: {p.get('alamat')}]"
                )
        if party_notes:
            lines.insert(0, "\n".join(party_notes) + "\n")
        return "\n".join(lines)

    def get_entity_hints(self, markdown_text: str) -> dict[str, Any]:
        """field_name -> value; dipakai sebagai fallback jika LLM tidak mengisi."""
        return self.analyze(markdown_text).hints
