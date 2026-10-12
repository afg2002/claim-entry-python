"""Deterministic claim classifications backed by the canonical rulebook.

This module keeps business decision logic separate from the raw extraction
registry and the Pydantic rulebook. The idea is simple:

- raw facts are described in the field registry
- business rules live in the rulebook
- this module translates those inputs into canonical decisions
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Sequence


def _rulebook(rulebook: Any | None = None) -> Any:
    """Return a provided rulebook or the default project rulebook."""
    if rulebook is not None:
        return rulebook
    from schemas.claim_rulebook import DEFAULT_RULEBOOK

    return DEFAULT_RULEBOOK


def normalized_label(value: Any) -> str:
    """Normalize a label for comparison without altering the stored raw fact."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _normalized_values(labels: Sequence[str]) -> set[str]:
    """Return a normalized set of configured labels for equality checks."""
    return {normalized_label(label) for label in labels}


def _label_matches(value: Any, labels: Sequence[str]) -> bool:
    """Return True when a raw value matches any configured label."""
    normalized = normalized_label(value)
    return normalized in _normalized_values(labels)


def _matching_labels(value: str, labels: Sequence[str]) -> tuple[str, ...]:
    """Return the subset of labels that match the normalized input text."""
    normalized = normalized_label(value)
    return tuple(
        label for label in labels if _label_in_text(normalized, label)
    )


def _label_in_text(value: str, label: str) -> bool:
    """Match a normalized label inside a normalized text body."""
    normalized = normalized_label(value)
    target = normalized_label(label)
    if not normalized or not target:
        return False
    return bool(
        re.search(rf"(?<![a-z0-9]){re.escape(target)}(?![a-z0-9])", normalized)
    )


def _visit_label_map(rulebook: Any) -> tuple[tuple[str, Sequence[str]], ...]:
    """Return the canonical visit-type mapping defined by the rulebook."""
    return (
        ("inpatient", rulebook.visit.inpatient_labels),
        ("outpatient", rulebook.visit.outpatient_labels),
    )


def canonical_visit_type(value: Any, rulebook: Any | None = None) -> str | None:
    """Map an explicit visit label to the canonical inpatient/outpatient value."""
    rb = _rulebook(rulebook)
    for canonical_value, labels in _visit_label_map(rb):
        if _label_matches(value, labels):
            return canonical_value
    return None


def canonical_claim_case_type(
    value: Any, rulebook: Any | None = None
) -> tuple[str, tuple[str, ...]] | None:
    """Return the canonical claim type and the matched labels when explicit."""
    normalized = normalized_label(value)
    for rule in _rulebook(rulebook).claim_types:
        matches = _matching_labels(normalized, rule.labels)
        if matches:
            return rule.value, matches
    return None


def _provider_country_match(
    provider_name: Any, country_hints: dict[str, list[str]]
) -> tuple[str | None, str | None]:
    """Return the country and matching hint when a provider name matches a known overseas rule."""
    lowered = str(provider_name or "").lower()
    for country, hints in (country_hints or {}).items():
        if any(str(hint).lower() in lowered for hint in hints):
            return country, next(
                str(hint).lower() for hint in hints if str(hint).lower() in lowered
            )
    return None, None


def detect_overseas(
    provider_name: Any, currency: Any, rulebook: Any | None = None
) -> tuple[bool, str | None, str]:
    """Detect overseas claims from provider hints and non-IDR currency signals."""
    rb = _rulebook(rulebook)
    country, _ = _provider_country_match(provider_name, rb.overseas.country_hints)
    if country:
        return True, country, f"Provider {provider_name} terdeteksi di {country}"

    if currency and str(currency).upper() not in ("IDR", "", "RUPIAH"):
        return True, None, f"Currency {currency} non-IDR menandakan klaim overseas"

    return False, None, "Provider di Indonesia, currency IDR"


def overseas_currency_fallback(
    provider_name: Any, rulebook: Any | None = None
) -> tuple[str | None, str | None]:
    """Return the configured fallback currency for a recognized overseas provider."""
    rb = _rulebook(rulebook)
    country, _ = _provider_country_match(provider_name, rb.overseas.country_hints)
    if country:
        return (rb.overseas.country_currency or {}).get(country), country
    return None, None


def name_similarity_pct(first: Any, second: Any) -> float:
    """Return a normalized name similarity score in the range 0..100."""

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
    """Collect configured inpatient and outpatient markers found in page text."""
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


def _document_type_evidence(
    present: set[str],
    inpatient_docs: frozenset[str],
    outpatient_docs: frozenset[str],
) -> tuple[str | None, str, list[str]]:
    """Resolve document-category evidence before falling back to text markers."""
    evidence: list[str] = []

    if present & inpatient_docs:
        evidence.append("dokumen rawat inap: " + ", ".join(sorted(present & inpatient_docs)))
        if present & outpatient_docs:
            return None, "mixed_document_evidence", evidence
        return "inpatient", "inpatient_document_type", evidence

    if present & outpatient_docs:
        evidence.append("dokumen kwitansi/invoice")
        if not present & inpatient_docs:
            return "outpatient", "outpatient_receipt_evidence", evidence

    return None, "insufficient_document_evidence", evidence


def visit_type_from_documents(
    document_types: Sequence[str | None],
    *,
    page_texts: Sequence[str | None] = (),
    rulebook: Any | None = None,
) -> tuple[str | None, str, list[str]]:
    """Infer visit type from document category and page-text evidence."""
    rb = _rulebook(rulebook)
    present = {value for value in document_types if value}
    inpatient_docs = frozenset(rb.documents.inpatient_document_types)
    outpatient_docs = frozenset(rb.documents.outpatient_document_types)

    visit_type, reason, evidence = _document_type_evidence(
        present, inpatient_docs, outpatient_docs
    )
    if visit_type is not None:
        return visit_type, reason, evidence

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
