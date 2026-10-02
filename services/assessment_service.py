"""Deterministic claim assessment from extracted facts and their evidence."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from schemas.claim_rulebook import DEFAULT_RULEBOOK
from services.claim_rules import (
    canonical_claim_case_type,
    canonical_visit_type,
    detect_overseas,
)


def _analysis_evidence(
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    field_name: str,
) -> list[dict[str, Any]]:
    return [dict(item) for item in field_evidence.get(field_name, [])]


def _analysis_evidence_snippets(evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        text
        for item in evidence
        if (text := str(item.get("source_text") or "").strip())
    ]


def _decision_evidence(
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    field_name: str,
    value: Any,
) -> list[str]:
    snippets = _analysis_evidence_snippets(
        _analysis_evidence(field_evidence, field_name)
    )
    if not snippets and value not in (None, "", []):
        return [f"{field_name}: {value}"]
    return snippets


def _decision(
    value: Any,
    confidence: float | None,
    method: str,
    reason: str,
    evidence: Sequence[str],
) -> dict[str, Any]:
    return {
        "value": value,
        "confidence": confidence,
        "method": method,
        "reason": reason,
        "evidence": list(evidence),
    }


def _tier(rulebook: Any | None, when: str, fallback: float | None) -> float | None:
    tiers = (rulebook or DEFAULT_RULEBOOK).confidence_tiers
    for tier in tiers:
        if tier.when == when:
            return tier.confidence
    return fallback


def _classify_visit_type(
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    rulebook: Any | None = None,
) -> dict[str, Any]:
    """Classify visit only from an explicit label or a strong date pair."""
    visit_evidence = _analysis_evidence(field_evidence, "visit_type")
    admission_evidence = _analysis_evidence(field_evidence, "admission_date")
    discharge_evidence = _analysis_evidence(field_evidence, "discharge_date")
    explicit_visit = claim_data.get("visit_type")

    if explicit_visit not in (None, ""):
        value = canonical_visit_type(explicit_visit, rulebook) or "unknown"
        return {
            "value": value,
            "status": "extracted" if value != "unknown" else "unknown",
            "confidence": (
                _tier(rulebook, "explicit_label", 100.0)
                if value != "unknown" else None
            ),
            "confidence_type": "grounded_rule" if value != "unknown" else None,
            "method": "explicit_document_value" if value != "unknown" else "unrecognized_explicit_value",
            "evidence": _analysis_evidence_snippets(visit_evidence),
            "evidence_refs": [item.get("extraction_id") for item in visit_evidence],
        }

    if claim_data.get("admission_date") and claim_data.get("discharge_date"):
        evidence = admission_evidence + discharge_evidence
        return {
            "value": "inpatient",
            "status": "derived",
            "confidence": _tier(rulebook, "admission+discharge+resume", 92.0),
            "confidence_type": "heuristic_not_calibrated",
            "method": "admission_and_discharge_dates_present",
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
        }

    incomplete_date_evidence = admission_evidence + discharge_evidence
    return {
        "value": "unknown",
        "status": "unknown",
        "confidence": None,
        "confidence_type": None,
        "method": "insufficient_explicit_evidence",
        "evidence": _analysis_evidence_snippets(incomplete_date_evidence),
        "evidence_refs": [item.get("extraction_id") for item in incomplete_date_evidence],
        "note": "Invoice/resume/document type alone is not sufficient to determine visit type.",
    }


def _classify_claim_case_type(
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    rulebook: Any | None = None,
) -> dict[str, Any]:
    """Normalize explicit claim-stage labels without inventing a stage."""
    evidence = _analysis_evidence(field_evidence, "claim_type")
    raw_value = claim_data.get("claim_type")
    if raw_value in (None, ""):
        return {
            "value": "unknown",
            "status": "unknown",
            "confidence": None,
            "confidence_type": None,
            "method": "no_explicit_claim_type",
            "raw_value": None,
            "evidence": [],
            "evidence_refs": [],
            "note": "Document type or filename alone is not used to infer claim stage.",
        }

    raw_values = list(dict.fromkeys(
        str(item.get("source_text") or "").strip()
        for item in evidence
        if str(item.get("source_text") or "").strip()
    ))
    if not raw_values:
        raw_values = [str(raw_value).strip()]

    classifications = [
        (value, canonical_claim_case_type(value, rulebook)) for value in raw_values
    ]
    recognized_values = {
        result[0] for _, result in classifications if result is not None
    }
    if len(recognized_values) > 1:
        return {
            "value": "unknown",
            "status": "unknown",
            "confidence": None,
            "confidence_type": None,
            "method": "conflicting_explicit_claim_type_values",
            "raw_value": str(raw_value),
            "raw_values": raw_values,
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
            "reason": "Explicit claim labels do not resolve to one configured category.",
            "note": "Multiple claim-stage labels require analyst review.",
        }

    matched_value = next(iter(recognized_values), None)
    matched_terms = list(dict.fromkeys(
        label
        for _, result in classifications
        if result is not None
        for label in result[1]
    ))
    if matched_value is None:
        return {
            "value": "unknown",
            "status": "unknown",
            "confidence": None,
            "confidence_type": None,
            "method": "unrecognized_explicit_claim_type",
            "raw_value": str(raw_value),
            "raw_values": raw_values,
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
            "reason": "The explicit claim label is not in the configured business mapping.",
            "note": "The explicit claim-stage label is not in the configured business mapping.",
        }

    return {
        "value": matched_value,
        "status": "derived",
        "confidence": _tier(rulebook, "explicit_label", 100.0),
        "confidence_type": "grounded_rule",
        "method": "explicit_claim_type_mapping",
        "raw_value": str(raw_value),
        "raw_values": raw_values,
        "matched_terms": matched_terms,
        "evidence": _analysis_evidence_snippets(
            [
                item
                for item in evidence
                if (match := canonical_claim_case_type(
                    str(item.get("source_text") or ""), rulebook
                )) is not None and match[0] == matched_value
            ]
        ),
        "evidence_refs": [
            item.get("extraction_id")
            for item in evidence
            if (match := canonical_claim_case_type(
                str(item.get("source_text") or ""), rulebook
            )) is not None and match[0] == matched_value
        ],
        "reason": f"Explicit claim labels map to the {matched_value} category.",
    }


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _build_decisions(
    claim_data: Mapping[str, Any],
    normalized_claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    visit: Mapping[str, Any],
    claim_case: Mapping[str, Any],
    rulebook: Any | None,
) -> dict[str, dict[str, Any]]:
    rb = rulebook or DEFAULT_RULEBOOK
    visit_value = visit.get("value", "unknown")
    claim_value = claim_case.get("value", "unknown")
    visit_confidence = visit.get("confidence")
    claim_confidence = claim_case.get("confidence")
    visit_evidence = list(visit.get("evidence") or [])
    claim_evidence = list(claim_case.get("evidence") or [])

    provider = claim_data.get("provider_name")
    currency = normalized_claim_data.get("currency") or claim_data.get("currency")
    overseas_evidence = (
        _decision_evidence(field_evidence, "provider_name", provider)
        + _decision_evidence(field_evidence, "currency", currency)
    )
    overseas_value: bool | None = None
    overseas_confidence = None
    overseas_method = "insufficient_evidence"
    overseas_reason = "Provider atau currency belum cukup tersedia untuk menilai overseas."
    if provider or currency:
        overseas_value, country, overseas_reason = detect_overseas(
            provider, currency, rb
        )
        if overseas_value:
            tier = "overseas_provider_hint" if country else "explicit_label"
            overseas_confidence = _tier(rulebook, tier, 90.0 if country else 100.0)
            overseas_method = "rulebook_provider_hint" if country else "non_idr_currency"
        elif currency:
            overseas_confidence = 100.0
            overseas_method = "explicit_currency"
        else:
            overseas_value = None
            overseas_method = "insufficient_location_evidence"
            overseas_reason = "Nama provider tidak cocok dengan lokasi overseas yang dikonfigurasi."

    discharge = _as_date(
        normalized_claim_data.get("discharge_date") or claim_data.get("discharge_date")
    )
    invoice = _as_date(
        normalized_claim_data.get("invoice_date") or claim_data.get("invoice_date")
    )
    period_value: bool | None = None
    period_confidence = None
    period_reason = "Tanggal invoice dan discharge diperlukan untuk menilai possible new claim."
    period_evidence = (
        _decision_evidence(
            field_evidence,
            "discharge_date",
            normalized_claim_data.get("discharge_date") or claim_data.get("discharge_date"),
        )
        + _decision_evidence(
            field_evidence,
            "invoice_date",
            normalized_claim_data.get("invoice_date") or claim_data.get("invoice_date"),
        )
    )
    if discharge and invoice:
        days_after_discharge = (invoice - discharge).days
        threshold = rb.period.max_days_after_discharge
        period_value = days_after_discharge > threshold
        period_confidence = _tier(rulebook, "period_rule", 90.0)
        comparison = "lebih dari" if period_value else "tidak lebih dari"
        period_reason = (
            f"Invoice terbit {days_after_discharge} hari setelah discharge, "
            f"{comparison} ambang {threshold} hari."
        )

    return {
        "visitType": _decision(
            visit_value,
            visit_confidence,
            str(visit.get("method") or "assessment"),
            str(visit.get("reason") or "; ".join(visit_evidence) or visit.get("note") or "Bukti tipe kunjungan belum cukup."),
            visit_evidence,
        ),
        "isRI": _decision(
            visit_value == "inpatient" if visit_value != "unknown" else None,
            visit_confidence,
            "derived_from_visit_type",
            "Tipe kunjungan rawat inap." if visit_value == "inpatient" else "Tipe kunjungan bukan rawat inap." if visit_value == "outpatient" else "Tipe kunjungan belum dapat ditentukan.",
            visit_evidence,
        ),
        "isRJ": _decision(
            visit_value == "outpatient" if visit_value != "unknown" else None,
            visit_confidence,
            "derived_from_visit_type",
            "Tipe kunjungan rawat jalan." if visit_value == "outpatient" else "Tipe kunjungan bukan rawat jalan." if visit_value == "inpatient" else "Tipe kunjungan belum dapat ditentukan.",
            visit_evidence,
        ),
        "claimType": _decision(
            claim_value,
            claim_confidence,
            str(claim_case.get("method") or "claim_type_mapping"),
            str(claim_case.get("reason") or claim_case.get("note") or "Kategori klaim belum dapat ditentukan."),
            claim_evidence,
        ),
        "isNewClaim": _decision(
            claim_value == "new" if claim_value != "unknown" else None,
            claim_confidence,
            "derived_from_claim_type",
            "Kategori klaim adalah new." if claim_value == "new" else "Kategori klaim bukan new." if claim_value == "completeness" else "Kategori klaim belum dapat ditentukan.",
            claim_evidence,
        ),
        "isKelengkapan": _decision(
            claim_value == "completeness" if claim_value != "unknown" else None,
            claim_confidence,
            "derived_from_claim_type",
            "Kategori klaim adalah completeness." if claim_value == "completeness" else "Kategori klaim bukan completeness." if claim_value == "new" else "Kategori klaim belum dapat ditentukan.",
            claim_evidence,
        ),
        "isOverseas": _decision(
            overseas_value,
            overseas_confidence,
            overseas_method,
            overseas_reason,
            overseas_evidence,
        ),
        "possibleNewClaim": _decision(
            period_value,
            period_confidence,
            "rulebook_period_threshold" if period_value is not None else "insufficient_date_evidence",
            period_reason,
            period_evidence,
        ),
    }


def derive_claim_analysis(
    claim_data: Mapping[str, Any],
    normalized_claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    validation: Mapping[str, Any],
    *,
    icd_suggestions: Sequence[Mapping[str, Any]] | None = None,
    rulebook: Any | None = None,
    decisions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build analyst values from canonical facts, validation, and evidence."""
    visit_analysis = _classify_visit_type(claim_data, field_evidence, rulebook)
    claim_case_analysis = _classify_claim_case_type(claim_data, field_evidence, rulebook)
    decisions = _build_decisions(
        claim_data,
        normalized_claim_data,
        field_evidence,
        visit_analysis,
        claim_case_analysis,
        rulebook,
    )

    required_checks = (
        ("provider_name", "Provider name"),
        ("invoice_number", "Invoice number"),
        ("admission_date", "Admission date"),
        ("discharge_date", "Discharge date"),
        ("diagnosis", "Diagnosis"),
    )
    missing_fields = [
        field_name
        for field_name, _ in required_checks
        if claim_data.get(field_name) in (None, [], "")
    ]
    missing_information = [
        label for field_name, label in required_checks if field_name in missing_fields
    ]

    inconsistencies = list(validation.get("inconsistencies", []))
    for conflict in validation.get("conflicts", []):
        inconsistencies.append(
            f"Multiple explicit values found for {conflict['field']}: {', '.join(conflict['values'])}."
        )

    potential_risks: list[str] = []
    if claim_data.get("diagnosis") and not claim_data.get("icd_codes"):
        potential_risks.append("No explicit ICD code was found for the extracted diagnosis.")
    if validation.get("ungrounded_item_count", 0):
        potential_risks.append("Some model output was not grounded to the Markdown source.")
    if validation.get("conflicts"):
        potential_risks.append("Conflicting explicit values require analyst review.")
    if "Provider name" in missing_information:
        potential_risks.append("Provider name is missing from the available evidence.")
    if "Invoice number" in missing_information:
        potential_risks.append("Invoice number is missing from the available evidence.")

    fraud_indicators = [
        inconsistency
        for inconsistency in inconsistencies
        if any(term in inconsistency.lower() for term in ("date", "total", "multiple explicit"))
    ]
    diagnosis_values = list(claim_data.get("diagnosis", []))
    medication_values = list(claim_data.get("medications", []))
    procedure_values = list(claim_data.get("procedures", []))
    financial_parts: list[str] = []
    for field_name, label in (
        ("total_amount", "total amount"),
        ("currency", "currency"),
        ("invoice_number", "invoice"),
    ):
        if claim_data.get(field_name) is not None:
            financial_parts.append(f"{label} {claim_data[field_name]}")

    case_snapshot = (
        f"Extracted {len(diagnosis_values)} diagnosis record(s), "
        f"{len(medication_values)} medication record(s), and "
        f"{len(procedure_values)} procedure record(s). Visit type: "
        f"{visit_analysis['value']} ({visit_analysis['status']})."
    )
    medical_summary = (
        "Explicit diagnoses: " + ", ".join(diagnosis_values) + ". "
        "Explicit procedures: " + (", ".join(procedure_values) if procedure_values else "none") + "."
    )
    financial_summary = (
        "Explicit financial evidence: "
        + (", ".join(financial_parts) if financial_parts else "none")
        + "."
    )
    claim_story = (
        f"The available Markdown contains {len(diagnosis_values)} diagnosis item(s) and "
        f"{len(medication_values)} grouped medication item(s). "
        f"Derived visit classification is {visit_analysis['value']} with status {visit_analysis['status']}."
    )

    return {
        "visit_type": visit_analysis,
        "claim_case_type": claim_case_analysis,
        "decisions": {**decisions, **dict(decisions or {})},
        "missing_information": missing_information,
        "missing_fields": missing_fields,
        "inconsistencies": inconsistencies,
        "potential_risks": potential_risks,
        "fraud_indicators": fraud_indicators,
        "investigation_recommended": bool(inconsistencies),
        "icd_suggestions": [dict(item) for item in (icd_suggestions or [])],
        "case_snapshot": case_snapshot,
        "medical_summary": medical_summary,
        "financial_summary": financial_summary,
        "claim_story": claim_story,
        "summary_status": "derived",
    }


__all__ = ["derive_claim_analysis"]