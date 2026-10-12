"""Single-door claim rulebook (Pydantic-first).

This is the only place where business rules and their schema are defined. Each
field is described with a concise `Field(description=...)`, which flows into
prompt generation, JSON schema generation, and validation automatically.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Default values are centralized here so new rules are easier to scan and update
# without having to read many nested lambda blocks in the model fields.
DEFAULT_INPATIENT_LABELS = ["rawat inap", "inpatient"]
DEFAULT_OUTPATIENT_LABELS = ["rawat jalan", "outpatient"]
DEFAULT_INPATIENT_DOCUMENT_TYPES = ["resume", "discharge_letter"]
DEFAULT_OUTPATIENT_DOCUMENT_TYPES = ["invoice"]
DEFAULT_INPATIENT_TEXT_MARKERS = [
    "rawat inap",
    "perawatan inap",
    "inpatient",
    "tanggal masuk",
    "tanggal keluar",
    "hari rawat",
    "kamar perawatan",
    "ruang intensif",
    "icu",
]
DEFAULT_OUTPATIENT_TEXT_MARKERS = [
    "rawat jalan",
    "kwitansi rawat jalan",
    "kwitansi",
    "rincian tagihan",
    "invoice",
    "poliklinik",
    "igd",
    "outpatient",
]
DEFAULT_OVERSEAS_HINTS = {
    "Singapore": ["singapore", "mount elizabeth", "gleneagles"],
    "Malaysia": ["malaysia", "kuala lumpur"],
    "Korea": ["korea", "seoul"],
}
DEFAULT_OVERSEAS_CURRENCIES = {
    "Singapore": "SGD",
    "Malaysia": "MYR",
    "Korea": "KRW",
}
DEFAULT_NAME_CHECK_FIELDS = ["policy_holder_name", "patient_name"]


class ClaimTypeRule(BaseModel):
    """One claim-stage category and its explicit document labels."""

    model_config = ConfigDict(extra="forbid")

    value: Literal["new", "completeness"] = Field(
        description="Kategori kanonis: new | completeness."
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Label eksplisit di dokumen yang memetakan ke kategori ini.",
    )


class VisitRules(BaseModel):
    """Rawat inap vs rawat jalan."""

    model_config = ConfigDict(extra="forbid")

    inpatient_labels: list[str] = Field(
        default_factory=lambda: DEFAULT_INPATIENT_LABELS.copy(),
        description="Label eksplisit rawat inap.",
    )
    outpatient_labels: list[str] = Field(
        default_factory=lambda: DEFAULT_OUTPATIENT_LABELS.copy(),
        description="Label eksplisit rawat jalan.",
    )
    inpatient_must_be_in_stay: bool = Field(
        default=True,
        description="Kwitansi RI harus dalam rentang admission-discharge.",
    )
    outpatient_receipt_can_be_stay: bool = Field(
        default=True,
        description="RJ boleh memakai tanggal kwitansi sbg admission=discharge.",
    )
    resume_is_primary_for_ri: bool = Field(
        default=True,
        description="Referensi utama RI adalah Resume Medis bila admission kosong.",
    )


class OverseasRule(BaseModel):
    """Deteksi klaim luar negeri + fallback currency."""

    model_config = ConfigDict(extra="forbid")

    country_hints: dict[str, list[str]] = Field(
        default_factory=lambda: {country: hints.copy() for country, hints in DEFAULT_OVERSEAS_HINTS.items()},
        description="Kata kunci nama provider per negara untuk deteksi isOverseas.",
    )
    country_currency: dict[str, str] = Field(
        default_factory=lambda: DEFAULT_OVERSEAS_CURRENCIES.copy(),
        description="Fallback currency dari lokasi provider. Eksplisit dokumen menang.",
    )
    abroad_exception: Literal["warn_not_split", "split"] = Field(
        default="warn_not_split",
        description="Overseas boleh bayar di luar periode: warning, bukan split.",
    )


class PeriodRules(BaseModel):
    """Rules tied to discharge-period detection and claim recency."""

    model_config = ConfigDict(extra="forbid")

    max_days_after_discharge: int = Field(
        default=30,
        ge=0,
        description="Kwitansi >N hari setelah discharge jadi kandidat possibleNewClaim.",
    )


class NameCheckRule(BaseModel):
    """Validation for claimant name matching and insured identity checks."""

    model_config = ConfigDict(extra="forbid")

    mandatory: list[str] = Field(
        default_factory=lambda: DEFAULT_NAME_CHECK_FIELDS.copy(),
        description="Nama pemegang polis dan tertanggung wajib ada.",
    )
    threshold_pct: float = Field(
        default=85.0,
        ge=0,
        le=100,
        description="Similarity di bawah ini = mismatch, insured dikosongkan.",
    )


class ConfidenceTier(BaseModel):
    """Heuristic confidence band for downstream business decisions."""

    model_config = ConfigDict(extra="forbid")

    when: str = Field(description="Kondisi evidence.")
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Keyakinan 0-100, heuristik non-kalibrasi.",
    )


class Decision(BaseModel):
    """Derived decision output with reason and evidence for explainability."""

    model_config = ConfigDict(extra="forbid")

    value: str | bool | None = Field(description="Nilai keputusan atau null bila belum diketahui.")
    reason: str = Field(description="Alasan penentuan.")
    evidence: list[str] = Field(default_factory=list)
    method: str = Field(default="")
    confidence: float | None = Field(default=None, ge=0, le=100)


class DocumentRules(BaseModel):
    """Rules that map document categories and phrases to visit type."""

    model_config = ConfigDict(extra="forbid")

    inpatient_document_types: list[str] = Field(
        default_factory=lambda: DEFAULT_INPATIENT_DOCUMENT_TYPES.copy(),
        description="Kategori dokumen yang menandakan rawat inap.",
    )
    outpatient_document_types: list[str] = Field(
        default_factory=lambda: DEFAULT_OUTPATIENT_DOCUMENT_TYPES.copy(),
        description="Kategori dokumen kwitansi/invoice untuk rawat jalan.",
    )
    inpatient_text_markers: list[str] = Field(
        default_factory=lambda: DEFAULT_INPATIENT_TEXT_MARKERS.copy(),
        description="Frase fallback RI bila model tidak klasifikasi halaman.",
    )
    outpatient_text_markers: list[str] = Field(
        default_factory=lambda: DEFAULT_OUTPATIENT_TEXT_MARKERS.copy(),
        description="Frase fallback RJ bila model tidak klasifikasi halaman.",
    )


class ClaimSignals(BaseModel):
    """Aggregated claim status flags used by downstream assessment code."""

    model_config = ConfigDict(extra="forbid")

    isNewClaim: bool = False
    isKelengkapan: bool = False
    isRI: bool = False
    isRJ: bool = False
    isOverseas: bool = False
    possibleNewClaim: bool = False
    riConfidence: float | None = Field(default=None, ge=0, le=100)
    rjConfidence: float | None = Field(default=None, ge=0, le=100)


def _default_claim_type_rules() -> list[ClaimTypeRule]:
    """Return the canonical claim-type mapping used by the rulebook."""
    return [
        ClaimTypeRule(
            value="completeness",
            labels=["kelengkapan", "berkas lengkap", "completeness"],
        ),
        ClaimTypeRule(
            value="new",
            labels=["klaim baru", "new claim", "new", "skd", "formulir", "reimbursement", "kasual"],
        ),
    ]


def _default_confidence_tiers() -> list[ConfidenceTier]:
    """Return the business confidence heuristics used in decision logic."""
    return [
        ConfidenceTier(when="explicit_label", confidence=100),
        ConfidenceTier(when="admission+discharge+resume", confidence=92),
        ConfidenceTier(when="resume_only", confidence=80),
        ConfidenceTier(when="single_receipt_same_date", confidence=65),
        ConfidenceTier(when="overseas_provider_hint", confidence=90),
        ConfidenceTier(when="period_rule", confidence=90),
        ConfidenceTier(when="mixed_or_insufficient", confidence=None),
    ]


class ClaimRulebook(BaseModel):
    """Root rulebook. Edit di sini saja."""

    model_config = ConfigDict(extra="forbid")

    claim_types: list[ClaimTypeRule] = Field(default_factory=lambda: [
        ClaimTypeRule(value=rule.value, labels=list(rule.labels))
        for rule in _default_claim_type_rules()
    ])
    visit: VisitRules = Field(default_factory=VisitRules)
    documents: DocumentRules = Field(default_factory=DocumentRules)
    overseas: OverseasRule = Field(default_factory=OverseasRule)
    period: PeriodRules = Field(default_factory=PeriodRules)
    name_check: NameCheckRule = Field(default_factory=NameCheckRule)
    confidence_tiers: list[ConfidenceTier] = Field(default_factory=lambda: [
        ConfidenceTier(when=tier.when, confidence=tier.confidence)
        for tier in _default_confidence_tiers()
    ])


DEFAULT_RULEBOOK = ClaimRulebook()


def _format_rulebook_summary(rb: ClaimRulebook) -> list[str]:
    """Return human-readable rule summary lines for prompt generation."""
    lines = ["Business rules (generated from ClaimRulebook):"]
    for ct in rb.claim_types:
        lines.append(f"- claim_type={ct.value}: labels [{', '.join(ct.labels)}]")
    lines.append(
        "- RI: admission+discharge (+resume utama bila admission kosong); "
        f"kwitansi harus dalam periode: {rb.visit.inpatient_must_be_in_stay}"
    )
    lines.append(
        "- RJ: tanpa admission/discharge pakai hospitalization/kwitansi, "
        f"admission=discharge: {rb.visit.outpatient_receipt_can_be_stay}"
    )
    lines.append(
        f"- overseas fallback: {rb.overseas.country_currency}; "
        f"exception: {rb.overseas.abroad_exception}"
    )
    lines.append(
        f"- name_check: {rb.name_check.threshold_pct}%; "
        f"max_days_after_discharge: {rb.period.max_days_after_discharge}"
    )
    lines.append("- tiers: " + ", ".join(
        f"{tier.when}={tier.confidence}" for tier in rb.confidence_tiers))
    return lines


def rulebook_prompt_section(rulebook: ClaimRulebook | None = None) -> str:
    """Generate prompt text from the rulebook. Update the model -> prompt follows automatically."""
    rb = rulebook or DEFAULT_RULEBOOK
    return "\n".join(_format_rulebook_summary(rb))


def validate_rulebook(data: dict[str, Any]) -> ClaimRulebook:
    """Validate a raw dictionary against the canonical claim rulebook schema."""
    return ClaimRulebook.model_validate(data)


__all__ = [
    "ClaimRulebook",
    "ClaimSignals",
    "ClaimTypeRule",
    "ConfidenceTier",
    "Decision",
    "DEFAULT_RULEBOOK",
    "DocumentRules",
    "NameCheckRule",
    "OverseasRule",
    "PeriodRules",
    "VisitRules",
    "rulebook_prompt_section",
    "validate_rulebook",
]

def rulebook_prompt_section(rulebook: ClaimRulebook | None = None) -> str:
    """Generate prompt text dari rulebook. Ubah model -> prompt ikut."""
    rb = rulebook or DEFAULT_RULEBOOK
    lines = ["Business rules (generated from ClaimRulebook):"]
    for ct in rb.claim_types:
        lines.append(
            f"- claim_type={ct.value}: labels [{', '.join(ct.labels)}]"
        )
    lines.append(
        "- RI: admission+discharge (+resume utama bila admission kosong); "
        f"kwitansi harus dalam periode: {rb.visit.inpatient_must_be_in_stay}"
    )
    lines.append(
        "- RJ: tanpa admission/discharge pakai hospitalization/kwitansi, "
        f"admission=discharge: {rb.visit.outpatient_receipt_can_be_stay}"
    )
    lines.append(
        f"- overseas fallback: {rb.overseas.country_currency}; "
        f"exception: {rb.overseas.abroad_exception}"
    )
    lines.append(
        f"- name_check: {rb.name_check.threshold_pct}%; "
        f"max_days_after_discharge: {rb.period.max_days_after_discharge}"
    )
    lines.append("- tiers: " + ", ".join(
        f"{t.when}={t.confidence}" for t in rb.confidence_tiers))
    return "\n".join(lines)


def validate_rulebook(data: dict[str, Any]) -> ClaimRulebook:
    return ClaimRulebook.model_validate(data)


__all__ = [
    "ClaimRulebook",
    "ClaimSignals",
    "ClaimTypeRule",
    "ConfidenceTier",
    "Decision",
    "DEFAULT_RULEBOOK",
    "DocumentRules",
    "NameCheckRule",
    "OverseasRule",
    "PeriodRules",
    "VisitRules",
    "rulebook_prompt_section",
    "validate_rulebook",
]
