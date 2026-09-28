"""Configurable deterministic rules for claim analyst classifications.

This module contains no LLM calls.  Developers add business synonyms and
categories here instead of editing LangExtract or the contract builder.

`claim_type` is an extracted fact only when the document explicitly contains
a label.  The canonical `claim_case_type` is a derived analyst value.
`visit_type` follows a conservative evidence policy:

* explicit visit label wins;
* admission_date + discharge_date is sufficient to derive inpatient;
* one date, invoice, resume, filename, or document type alone is not sufficient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class ClaimCaseRule:
    """One business category and its explicit document labels."""

    canonical_value: str
    labels: tuple[str, ...]


# Add a new category/alias here when the claim workflow changes.
# The tuple order is intentional: the first matching business rule wins.
CLAIM_CASE_TYPE_RULES: tuple[ClaimCaseRule, ...] = (
    ClaimCaseRule(
        canonical_value="review",
        labels=(
            "tinjau ulang",
            "tinjauan ulang",
            "review",
            "re-review",
            "reassessment",
            "sp4",
        ),
    ),
    ClaimCaseRule(
        canonical_value="completeness",
        labels=(
            "kelengkapan",
            "kelengkapan dokumen",
            "berkas lengkap",
            "lengkap",
            "completeness",
            "complete documents",
            "document completion",
        ),
    ),
    ClaimCaseRule(
        canonical_value="new",
        labels=(
            "pengajuan baru",
            "klaim baru",
            "new claim",
            "new",
            "skd",
            "formulir",
            "reimbursement",
            "kasual",
            "casualty",
        ),
    ),
)


VISIT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "inpatient": (
        "inpatient",
        "in patient",
        "rawat inap",
        "perawatan inap",
        "hospitalisation",
        "hospitalization",
    ),
    "outpatient": (
        "outpatient",
        "out patient",
        "rawat jalan",
        "ambulatory",
        "kunjungan rawat jalan",
    ),
}

# Document types are produced by the model, never by a keyword table.  These
# sets only say what a *classified* document implies about the visit type, so
# they never decide what a document actually is.
INPATIENT_DOCUMENT_TYPES: frozenset[str] = frozenset(
    {"resume", "discharge_letter"}
)
# An outpatient visit is evidenced by a receipt for a single service day rather
# than by a stay document.
OUTPATIENT_DOCUMENT_TYPES: frozenset[str] = frozenset({"invoice"})

# Phrases that mark a document as inpatient even without a resume title.  Used
# only as a fallback when the model classified nothing useful.
INPATIENT_TEXT_MARKERS: tuple[str, ...] = (
    "rawat inap",
    "perawatan inap",
    "inpatient",
    "tanggal masuk",
    "tanggal keluar",
    "hari rawat",
    "lama dirawat",
    "kamar perawatan",
    "ruang intensif",
    "icu",
)
OUTPATIENT_TEXT_MARKERS: tuple[str, ...] = (
    "rawat jalan",
    "kwitansi rawat jalan",
    "kunjungan rawat jalan",
    "kwitansi",
    "rincian tagihan",
    "invoice",
    "poliklinik",
    "emergency room",
    "igd",
    "outpatient",
)


def _text_markers_in(page_texts: Sequence[str | None]) -> tuple[set[str], set[str]]:
    """Collect the inpatient and outpatient phrases present in the bundle."""
    inpatient: set[str] = set()
    outpatient: set[str] = set()
    for text in page_texts:
        if not text:
            continue
        lowered = str(text).lower()
        for marker in INPATIENT_TEXT_MARKERS:
            if marker in lowered:
                inpatient.add(marker)
        for marker in OUTPATIENT_TEXT_MARKERS:
            if marker in lowered:
                outpatient.add(marker)
    return inpatient, outpatient





def normalized_label(value: Any) -> str:
    """Normalize a label for matching without changing the stored raw fact."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def canonical_visit_type(value: Any) -> str | None:
    """Return inpatient/outpatient for an explicitly supported label."""
    normalized = normalized_label(value)
    for canonical_value, aliases in VISIT_TYPE_ALIASES.items():
        if normalized in aliases:
            return canonical_value
    return None


def canonical_claim_case_type(value: Any) -> tuple[str, tuple[str, ...]] | None:
    """Return `(canonical_value, matched_labels)` for an explicit claim label."""
    normalized = normalized_label(value)
    for rule in CLAIM_CASE_TYPE_RULES:
        matches = tuple(
            label
            for label in rule.labels
            if re.search(
                rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])",
                normalized,
            )
        )
        if matches:
            return rule.canonical_value, matches
    return None


# ---------------------------------------------------------------------------
# Page / document labelling
# ---------------------------------------------------------------------------

def document_title(text: str | None) -> str | None:
    """Return the first non-empty line of a page, used as a human label."""
    if not text:
        return None
    for line in str(text).splitlines():
        candidate = line.strip(" \t#*-=")
        if len(candidate) >= 3:
            return candidate[:120]
    return None


def visit_type_from_documents(
    document_types: Sequence[str | None],
    *,
    page_texts: Sequence[str | None] = (),
) -> tuple[str | None, str, list[str]]:
    """Infer visit type from the kinds of documents in the bundle.

    Returns `(value, method, evidence)` where ``value`` is ``None`` when the
    evidence is not decisive.  A resume or discharge letter only exists for an
    overnight stay, while a lone receipt for a single service day indicates an
    outpatient visit; a mixed bundle stays unknown on purpose.
    """
    present = {value for value in document_types if value}
    evidence: list[str] = []
    if present & INPATIENT_DOCUMENT_TYPES:
        evidence.append(
            "dokumen rawat inap: " + ", ".join(sorted(present & INPATIENT_DOCUMENT_TYPES))
        )
        if present & OUTPATIENT_DOCUMENT_TYPES:
            # Both a stay document and a receipt are present; do not guess.
            return None, "mixed_document_evidence", evidence
        return "inpatient", "inpatient_document_type", evidence
    if present & OUTPATIENT_DOCUMENT_TYPES:
        evidence.append("dokumen kwitansi/invoice")
        if not present & INPATIENT_DOCUMENT_TYPES:
            return "outpatient", "outpatient_receipt_evidence", evidence

    # No decisive document type: fall back on the wording of the pages.
    inpatient_words, outpatient_words = _text_markers_in(page_texts)
    if inpatient_words:
        evidence.append("frasa rawat inap: " + ", ".join(sorted(inpatient_words)))
        if outpatient_words:
            # Both wordings appear; the bundle does not say which visit it was.
            return None, "mixed_document_evidence", evidence
        return "inpatient", "inpatient_text_marker", evidence
    if outpatient_words:
        evidence.append("frasa rawat jalan: " + ", ".join(sorted(outpatient_words)))
        return "outpatient", "outpatient_text_marker", evidence
    return None, "insufficient_document_evidence", evidence