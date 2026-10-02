"""Single-door claim rulebook (Pydantic-first).

Satu-satunya tempat developer menambah/mengubah rules + schema.
Setiap field membawa Field(description=...) sehingga otomatis menjadi:
prompt text, JSON schema ke LLM, validasi, dan docs UI.

Cara tambah rules: tambah 1 field + 1 kalimat description. Selesai.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
        default_factory=lambda: ["rawat inap", "inpatient"],
        description="Label eksplisit rawat inap.",
    )
    outpatient_labels: list[str] = Field(
        default_factory=lambda: ["rawat jalan", "outpatient"],
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
        default_factory=lambda: {
            "Singapore": ["singapore", "mount elizabeth", "gleneagles"],
            "Malaysia": ["malaysia", "kuala lumpur"],
            "Korea": ["korea", "seoul"],
        },
        description="Kata kunci nama provider per negara untuk deteksi isOverseas.",
    )
    country_currency: dict[str, str] = Field(
        default_factory=lambda: {
            "Singapore": "SGD",
            "Malaysia": "MYR",
            "Korea": "KRW",
        },
        description="Fallback currency dari lokasi provider. Eksplisit dokumen menang.",
    )
    abroad_exception: Literal["warn_not_split", "split"] = Field(
        default="warn_not_split",
        description="Overseas boleh bayar di luar periode: warning, bukan split.",
    )


class PeriodRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_days_after_discharge: int = Field(
        default=30,
        ge=0,
        description="Kwitansi >N hari setelah discharge jadi kandidat possibleNewClaim.",
    )


class NameCheckRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandatory: list[str] = Field(
        default_factory=lambda: ["policy_holder_name", "patient_name"],
        description="Nama pemegang polis dan tertanggung wajib ada.",
    )
    threshold_pct: float = Field(
        default=85.0,
        ge=0,
        le=100,
        description="Similarity di bawah ini = mismatch, insured dikosongkan.",
    )


class ConfidenceTier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: str = Field(description="Kondisi evidence.")
    confidence: float | None = Field(
        default=None, ge=0, le=100,
        description="Keyakinan 0-100, heuristik non-kalibrasi.",
    )


class Decision(BaseModel):
    """Satu keputusan derived + reason agar analyst tau kenapa."""

    model_config = ConfigDict(extra="forbid")

    value: str | bool | None = Field(description="Nilai keputusan atau null bila belum diketahui.")
    reason: str = Field(description="Alasan penentuan.")
    evidence: list[str] = Field(default_factory=list)
    method: str = Field(default="")
    confidence: float | None = Field(default=None, ge=0, le=100)


class DocumentRules(BaseModel):
    """Aturan dokumen -> visit type. Kategori dipakai logika, bukan label bebas."""

    model_config = ConfigDict(extra="forbid")

    inpatient_document_types: list[str] = Field(
        default_factory=lambda: ["resume", "discharge_letter"],
        description="Kategori dokumen yang menandakan rawat inap.",
    )
    outpatient_document_types: list[str] = Field(
        default_factory=lambda: ["invoice"],
        description="Kategori dokumen kwitansi/invoice untuk rawat jalan.",
    )
    inpatient_text_markers: list[str] = Field(
        default_factory=lambda: [
            "rawat inap", "perawatan inap", "inpatient",
            "tanggal masuk", "tanggal keluar", "hari rawat",
            "kamar perawatan", "ruang intensif", "icu",
        ],
        description="Frase fallback RI bila model tidak klasifikasi halaman.",
    )
    outpatient_text_markers: list[str] = Field(
        default_factory=lambda: [
            "rawat jalan", "kwitansi rawat jalan", "kwitansi",
            "rincian tagihan", "invoice", "poliklinik", "igd", "outpatient",
        ],
        description="Frase fallback RJ bila model tidak klasifikasi halaman.",
    )


class ClaimSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    isNewClaim: bool = False
    isKelengkapan: bool = False
    isRI: bool = False
    isRJ: bool = False
    isOverseas: bool = False
    possibleNewClaim: bool = False
    riConfidence: float | None = Field(default=None, ge=0, le=100)
    rjConfidence: float | None = Field(default=None, ge=0, le=100)


class ClaimRulebook(BaseModel):
    """Root rulebook. Edit di sini saja."""

    model_config = ConfigDict(extra="forbid")

    claim_types: list[ClaimTypeRule] = Field(
        default_factory=lambda: [
            ClaimTypeRule(
                value="completeness",
                labels=["kelengkapan", "berkas lengkap", "completeness"],
            ),
            ClaimTypeRule(
                value="new",
                labels=["klaim baru", "new claim", "new", "skd",
                        "formulir", "reimbursement", "kasual"],
            ),
        ]
    )
    visit: VisitRules = Field(default_factory=VisitRules)
    documents: DocumentRules = Field(default_factory=DocumentRules)
    overseas: OverseasRule = Field(default_factory=OverseasRule)
    period: PeriodRules = Field(default_factory=PeriodRules)
    name_check: NameCheckRule = Field(default_factory=NameCheckRule)
    confidence_tiers: list[ConfidenceTier] = Field(
        default_factory=lambda: [
            ConfidenceTier(when="explicit_label", confidence=100),
            ConfidenceTier(when="admission+discharge+resume", confidence=92),
            ConfidenceTier(when="resume_only", confidence=80),
            ConfidenceTier(when="single_receipt_same_date", confidence=65),
            ConfidenceTier(when="overseas_provider_hint", confidence=90),
            ConfidenceTier(when="period_rule", confidence=90),
            ConfidenceTier(when="mixed_or_insufficient", confidence=None),
        ]
    )


DEFAULT_RULEBOOK = ClaimRulebook()


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
