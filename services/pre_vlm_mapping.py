"""Cheap deterministic/fuzzy mapping before remote VLM.

The mapper is intentionally conservative: a fuzzy match can skip VLM only when
its score clears the configured threshold. Ambiguous/low-confidence text stays
unresolved so the existing VLM pipeline remains the source of truth.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

try:
    from rapidfuzz import fuzz, process
except ImportError:  # pragma: no cover - dependency is optional at import time
    fuzz = None
    process = None


DEFAULT_ALIASES: dict[str, tuple[str, ...]] = {
    "patient_name": ("nama pasien", "nama tertanggung", "insured name", "patient name"),
    "policy_holder_name": ("nama pemegang polis", "policy holder", "policyholder"),
    "member_number": ("nomor peserta", "no peserta", "member no", "member number", "no kartu"),
    "patient_id": ("nomor pasien", "no pasien", "patient id", "medical record", "no rm"),
    "date_of_birth": ("tanggal lahir", "tgl lahir", "date of birth", "dob"),
    "provider_name": ("nama rumah sakit", "rumah sakit", "hospital", "provider", "faskes"),
    "provider_code": ("kode rumah sakit", "kode provider", "provider code"),
    "admission_date": ("tanggal masuk", "tgl masuk", "admission date", "admitted"),
    "discharge_date": ("tanggal keluar", "tgl keluar", "discharge date", "discharged"),
    "total_amount": ("total tagihan", "total biaya", "jumlah tagihan", "total amount", "amount"),
    "currency": ("mata uang", "currency"),
    "icd_code_1": ("diagnosis utama", "icd utama", "primary diagnosis", "primary icd"),
    "icd_code_2": ("diagnosis sekunder", "icd sekunder", "secondary diagnosis", "secondary icd"),
}


@dataclass(frozen=True)
class MappingResult:
    field: str
    value: str | None
    method: str
    confidence: float
    matched_alias: str | None = None
    needs_vlm: bool = True
    evidence: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "method": self.method,
            "confidence": round(self.confidence, 4),
            "matched_alias": self.matched_alias,
            "needs_vlm": self.needs_vlm,
            "evidence": self.evidence,
        }


def normalize_text(value: str) -> str:
    """Normalize OCR text without destroying useful alphanumeric content."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"[^\w\s:/.,%]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _regex_extract(text: str, field: str) -> str | None:
    """Extract high-value fields using conservative label/value patterns."""
    patterns: dict[str, tuple[str, ...]] = {
        "member_number": (
            r"(?:no\.?\s*(?:peserta|kartu|member)|nomor\s*(?:peserta|kartu|member))\s*[:#-]?\s*([A-Z0-9][A-Z0-9./ -]{3,})",
        ),
        "patient_id": (
            r"(?:no\.?\s*(?:pasien|rm)|nomor\s*(?:pasien|rekam\s*medis)|medical\s*record)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./ -]{2,})",
        ),
        "date_of_birth": (
            r"(?:tanggal\s*lahir|tgl\.?\s*lahir|date\s*of\s*birth|dob)\s*[:#-]?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        ),
        "total_amount": (
            r"(?:total\s*(?:tagihan|biaya|amount)|jumlah\s*tagihan)\s*[:#-]?\s*(?:rp\.?\s*)?([\d.,]+)",
        ),
        "currency": (
            r"(?:mata\s*uang|currency)\s*[:#-]?\s*([A-Z]{3})\b",
        ),
        "provider_code": (
            r"(?:kode\s*(?:rumah\s*sakit|provider)|provider\s*code)\s*[:#-]?\s*([A-Z0-9._/-]{2,})",
        ),
    }
    for pattern in patterns.get(field, ()):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,-")
    return None


def _aliases(mapping: Mapping[str, Iterable[str]] | None = None) -> dict[str, tuple[str, ...]]:
    source = mapping or DEFAULT_ALIASES
    return {str(field): tuple(str(alias) for alias in aliases) for field, aliases in source.items()}


def map_field(
    text: str,
    field: str,
    *,
    aliases: Mapping[str, Iterable[str]] | None = None,
    fuzzy_threshold: float = 92.0,
    fuzzy_margin: float = 5.0,
    regex_enabled: bool = True,
    fuzzy_enabled: bool = True,
) -> MappingResult:
    """Map one field label in OCR text without invoking an LLM."""
    normalized = normalize_text(text)
    if not normalized:
        return MappingResult(field, None, "empty", 0.0, needs_vlm=True)

    if regex_enabled:
        regex_value = _regex_extract(normalized, field)
        if regex_value:
            return MappingResult(
                field, regex_value, "regex", 100.0, needs_vlm=False, evidence=regex_value
            )

    if not fuzzy_enabled:
        return MappingResult(field, None, "fuzzy_disabled", 0.0, needs_vlm=True)

    if process is None or fuzz is None:
        return MappingResult(field, None, "rapidfuzz_unavailable", 0.0, needs_vlm=True)

    candidates = _aliases(aliases).get(field, ())
    if not candidates:
        return MappingResult(field, None, "no_alias", 0.0, needs_vlm=True)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        lines = [normalized]
    choices = {alias: alias for alias in candidates}
    best: tuple[float, str, str] | None = None
    second = 0.0
    for raw_line in lines:
        line = normalize_text(raw_line)
        if not line:
            continue
        result = process.extract(line, choices, scorer=fuzz.token_set_ratio, limit=2)
        if not result:
            continue
        score, alias = float(result[0][1]), str(result[0][0])
        if best is None or score > best[0]:
            if best is not None:
                second = max(second, best[0])
            best = (score, alias, raw_line.strip())
        elif score > second:
            second = score

    if best is None:
        return MappingResult(field, None, "no_match", 0.0, needs_vlm=True)

    score, alias, evidence = best
    confident = score >= fuzzy_threshold and (score - second >= fuzzy_margin or second == 0)
    return MappingResult(
        field,
        evidence if confident else None,
        "rapidfuzz" if confident else "ambiguous_fuzzy",
        score,
        matched_alias=alias,
        needs_vlm=not confident,
        evidence=evidence,
    )


def map_fields(
    text: str,
    fields: Iterable[str],
    *,
    aliases: Mapping[str, Iterable[str]] | None = None,
    fuzzy_threshold: float = 92.0,
    fuzzy_margin: float = 5.0,
    regex_enabled: bool = True,
    fuzzy_enabled: bool = True,
) -> dict[str, dict[str, Any]]:
    return {
        field: map_field(
            text,
            field,
            aliases=aliases,
            fuzzy_threshold=fuzzy_threshold,
            fuzzy_margin=fuzzy_margin,
            regex_enabled=regex_enabled,
            fuzzy_enabled=fuzzy_enabled,
        ).as_dict()
        for field in fields
    }


def summarize_cost_saving(
    total_pages: int,
    skipped_vlm_pages: int,
    *,
    estimated_vlm_cost_per_page_idr: float = 0.0,
) -> dict[str, Any]:
    total_pages = max(0, int(total_pages))
    skipped_vlm_pages = min(total_pages, max(0, int(skipped_vlm_pages)))
    percentage = (skipped_vlm_pages / total_pages * 100) if total_pages else 0.0
    return {
        "total_pages": total_pages,
        "vlm_pages": total_pages - skipped_vlm_pages,
        "skipped_vlm_pages": skipped_vlm_pages,
        "vlm_skip_rate": round(percentage, 2),
        "estimated_cost_saved_idr": round(skipped_vlm_pages * estimated_vlm_cost_per_page_idr, 2),
    }
