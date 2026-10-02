"""Deterministic claim classifications backed by the canonical rulebook."""
from __future__ import annotations

import re
from typing import Any, Sequence


def _rulebook(rulebook: Any | None = None) -> Any:
    if rulebook is not None:
        return rulebook
    from schemas.claim_rulebook import DEFAULT_RULEBOOK

    return DEFAULT_RULEBOOK


def normalized_label(value: Any) -> str:
    """Normalize a label for matching without changing the stored raw fact."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def canonical_visit_type(value: Any, rulebook: Any | None = None) -> str | None:
    """Return inpatient/outpatient for an explicitly supported label."""
    normalized = normalized_label(value)
    rb = _rulebook(rulebook)
    for canonical_value, labels in (
        ("inpatient", rb.visit.inpatient_labels),
        ("outpatient", rb.visit.outpatient_labels),
    ):
        if normalized in {normalized_label(label) for label in labels}:
            return canonical_value
    return None


def canonical_claim_case_type(
    value: Any, rulebook: Any | None = None
) -> tuple[str, tuple[str, ...]] | None:
    """Return `(canonical_value, matched_labels)` for an explicit claim label."""
    normalized = normalized_label(value)
    for rule in _rulebook(rulebook).claim_types:
        matches = tuple(
            label
            for label in rule.labels
            if re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_label(label))}(?![a-z0-9])",
                normalized,
            )
        )
        if matches:
            return rule.value, matches
    return None


def detect_overseas(
    provider_name: Any, currency: Any, rulebook: Any | None = None
) -> tuple[bool, str | None, str]:
    """Detect overseas claims from provider name and currency."""
    rb = _rulebook(rulebook)
    lowered = str(provider_name or "").lower()
    for country, hints in (rb.overseas.country_hints or {}).items():
        if any(str(hint).lower() in lowered for hint in hints):
            return True, country, f"Provider {provider_name} terdeteksi di {country}"
    if currency and str(currency).upper() not in ("IDR", "", "RUPIAH"):
        return True, None, f"Currency {currency} non-IDR menandakan klaim overseas"
    return False, None, "Provider di Indonesia, currency IDR"


def overseas_currency_fallback(
    provider_name: Any, rulebook: Any | None = None
) -> tuple[str | None, str | None]:
    """Return the configured currency fallback for a recognized provider."""
    rb = _rulebook(rulebook)
    lowered = str(provider_name or "").lower()
    for country, hints in (rb.overseas.country_hints or {}).items():
        if any(str(hint).lower() in lowered for hint in hints):
            return (rb.overseas.country_currency or {}).get(country), country
    return None, None


def name_similarity_pct(first: Any, second: Any) -> float:
    """Return a normalized name similarity score from 0 to 100."""
    import difflib

    def normalize(value: Any) -> str:
        text = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
        return re.sub(r"\s+", " ", text).strip()

    left, right = normalize(first), normalize(second)
    if not left or not right:
        return 0.0
    if left == right:
        return 100.0
    return round(difflib.SequenceMatcher(None, left, right).ratio() * 100, 1)


def document_title(text: str | None) -> str | None:
    """Return the first non-empty line of a page, used as a human label."""
    if not text:
        return None
    for line in str(text).splitlines():
        candidate = line.strip(" \t#*-= ")
        if len(candidate) >= 3:
            return candidate[:120]
    return None


def _text_markers_in(
    page_texts: Sequence[str | None], rulebook: Any | None = None
) -> tuple[set[str], set[str]]:
    """Collect configured inpatient and outpatient phrases in the pages."""
    rb = _rulebook(rulebook)
    inpatient: set[str] = set()
    outpatient: set[str] = set()
    for text in page_texts:
        if not text:
            continue
        lowered = str(text).lower()
        inpatient.update(
            marker for marker in rb.documents.inpatient_text_markers
            if marker in lowered
        )
        outpatient.update(
            marker for marker in rb.documents.outpatient_text_markers
            if marker in lowered
        )
    return inpatient, outpatient


def visit_type_from_documents(
    document_types: Sequence[str | None],
    *,
    page_texts: Sequence[str | None] = (),
    rulebook: Any | None = None,
) -> tuple[str | None, str, list[str]]:
    """Infer visit type from configured document categories and text evidence."""
    rb = _rulebook(rulebook)
    present = {value for value in document_types if value}
    inpatient_docs = frozenset(rb.documents.inpatient_document_types)
    outpatient_docs = frozenset(rb.documents.outpatient_document_types)
    evidence: list[str] = []
    if present & inpatient_docs:
        evidence.append(
            "dokumen rawat inap: " + ", ".join(sorted(present & inpatient_docs))
        )
        if present & outpatient_docs:
            return None, "mixed_document_evidence", evidence
        return "inpatient", "inpatient_document_type", evidence
    if present & outpatient_docs:
        evidence.append("dokumen kwitansi/invoice")
        if not present & inpatient_docs:
            return "outpatient", "outpatient_receipt_evidence", evidence

    inpatient_words, outpatient_words = _text_markers_in(page_texts, rb)
    if inpatient_words:
        evidence.append("frasa rawat inap: " + ", ".join(sorted(inpatient_words)))
        if outpatient_words:
            return None, "mixed_document_evidence", evidence
        return "inpatient", "inpatient_text_marker", evidence
    if outpatient_words:
        evidence.append("frasa rawat jalan: " + ", ".join(sorted(outpatient_words)))
        return "outpatient", "outpatient_text_marker", evidence
    return None, "insufficient_document_evidence", evidence