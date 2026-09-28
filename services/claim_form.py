"""Compact, form-shaped claim JSON built from the auditable claim contract.

The full contract in ``services.langextract_service`` is deliberately verbose:
it keeps every grounded item, every evidence span, and every derived
classification so a reviewer can retrace any value.  That is the right shape
for audit, but it is the wrong shape for a claim form or an API payload.

This module projects the contract into the blocks a claim officer actually
reads, following the layout of the claim form:

``document`` · ``member_info`` · ``policy_info`` · ``claim`` · ``visit`` ·
``provider`` · ``medical`` · ``financial`` · ``service_lines`` ·
``medications`` · ``procedures`` · ``lab_results`` · ``review``

Design rules:

* values are copied from ``claim_data``/``normalized_claim_data``; nothing is
  invented here;
* defaults that the form itself defines (IDR and an exchange rate of 1) are
  applied and marked as ``default`` rather than presented as document facts;
* every derived value keeps its ``status`` so a reader can tell an extracted
  fact from a computed one;
* absent values stay ``null`` so the JSON shape is stable between runs.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from services.claim_rules import (
    document_title,
    visit_type_from_documents,
)

CLAIM_FORM_VERSION = "claim-form-v1"
DEFAULT_CURRENCY = "IDR"
DEFAULT_EXCHANGE_RATE = 1


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _raw(claim_data: Mapping[str, Any], key: str) -> Any:
    value = claim_data.get(key)
    return value if value not in ("",) else None


def _value(claim_data: Mapping[str, Any], normalized: Mapping[str, Any], key: str) -> Any:
    """Prefer the normalized value, fall back to the verbatim document text."""
    normalized_value = normalized.get(key)
    if normalized_value not in (None, ""):
        return normalized_value
    return _raw(claim_data, key)


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _length_of_stay(admission: Any, discharge: Any) -> int | None:
    if not admission or not discharge:
        return None
    try:
        return (date.fromisoformat(str(discharge)) - date.fromisoformat(str(admission))).days
    except (TypeError, ValueError):
        return None


def _page_inventory(
    pages: Sequence[Mapping[str, Any]] | None,
    classifications: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the document inventory from the model's own page names.

    ``document_type`` is the name the model gave, kept verbatim. ``category`` is
    the narrow business key used for workflow logic and may be ``null``. When no
    classification ran (extraction disabled) the page title is shown as the
    name so the inventory still reads as something rather than a blank row.
    """
    by_page = {
        str(item.get("page")): item
        for item in (classifications or [])
        if isinstance(item, Mapping) and item.get("page") is not None
    }
    inventory: list[dict[str, Any]] = []
    for page in pages or []:
        text = page.get("raw_text") or page.get("text")
        resolved = by_page.get(str(page.get("page_number")))
        if resolved is None:
            # No model answer for this page: show the page's own title rather
            # than inventing a document type.
            inventory.append({
                "page": page.get("page_number"),
                "document_type": document_title(text),
                "category": None,
                "label": document_title(text),
                "source": "page_title",
            })
            continue
        document_type = resolved.get("document_type")
        inventory.append({
            "page": page.get("page_number"),
            "document_type": document_type,
            "category": resolved.get("category"),
            "label": resolved.get("label") or document_title(text),
            "source": resolved.get("source", "model"),
        })
    return {"total_pages": len(inventory), "pages": inventory}


# ---------------------------------------------------------------------------
# Visit block
# ---------------------------------------------------------------------------

def _visit_block(
    claim_data: Mapping[str, Any],
    normalized: Mapping[str, Any],
    analyst_analysis: Mapping[str, Any],
    pages: Sequence[Mapping[str, Any]] | None,
    classifications: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    visit_analysis = analyst_analysis.get("visit_type") or {}
    visit_type = visit_analysis.get("value") or "unknown"
    status = visit_analysis.get("status") or "unknown"
    method = visit_analysis.get("method")
    evidence = list(visit_analysis.get("evidence") or [])

    # The explicit label and the date pair stay authoritative; the classified
    # categories are only consulted when they produced nothing.
    if visit_type == "unknown":
        categories = [item.get("category") for item in (classifications or [])]
        page_texts = [page.get("raw_text") or page.get("text") for page in pages or []]
        inferred, doc_method, doc_evidence = visit_type_from_documents(
            categories, page_texts=page_texts
        )
        if inferred is not None:
            visit_type = inferred
            status = "derived"
            method = doc_method
            evidence = doc_evidence

    admission = _value(claim_data, normalized, "admission_date")
    discharge = _value(claim_data, normalized, "discharge_date")
    hospitalization = _value(claim_data, normalized, "hospitalization_date")
    no_of_days = _first(_length_of_stay(admission, discharge), 0 if visit_type == "outpatient" else None)

    block: dict[str, Any] = {
        "type_of_visit": visit_type,
        "status": status,
        "hospitalization_date": hospitalization,
        "admission_date": admission,
        "discharge_date": discharge,
        "no_of_days": no_of_days,
        "method": method,
        "evidence": evidence,
    }
    if visit_type == "unknown":
        block["note"] = (
            "Tipe kunjungan belum dapat ditentukan. Dokumen resume/surat keluar "
            "menandakan rawat inap, sedangkan kwitansi tunggal menandakan rawat "
            "jalan."
        )
    return block


# ---------------------------------------------------------------------------
# Reason of visit
# ---------------------------------------------------------------------------

def _reason_of_visit(
    claim_data: Mapping[str, Any],
    visit_block: Mapping[str, Any],
) -> dict[str, Any]:
    """Compose a short narrative from grounded facts only.

    The claim form asks for a reason of visit, and an inpatient claim is
    expected to read as a story rather than a bare label.  The sentence is
    assembled from values that are already grounded in the document, so the
    text can never introduce a fact that the extraction stage did not find.  An
    explicit ``reason_of_visit`` written in the document always wins.
    """
    explicit = _raw(claim_data, "reason_of_visit")
    if explicit:
        return {"reason_of_visit": explicit, "reason_status": "extracted"}

    diagnosis = list(claim_data.get("diagnosis") or [])
    procedures = list(claim_data.get("procedures") or [])
    treatments = list(claim_data.get("treatments") or [])
    visit_type = visit_block.get("type_of_visit")
    hospitalization = visit_block.get("hospitalization_date")
    admission = visit_block.get("admission_date")
    discharge = visit_block.get("discharge_date")
    provider = _raw(claim_data, "provider_name")
    doctor = _raw(claim_data, "doctor_name")

    parts: list[str] = []
    if visit_type == "inpatient":
        parts.append("Rawat inap")
    elif visit_type == "outpatient":
        parts.append("Rawat jalan")

    # An inpatient stay is described by its admission/discharge window, so the
    # stay dates take precedence over the single service date.
    if admission and discharge:
        parts.append(f"periode {admission} sampai {discharge}")
    elif hospitalization:
        parts.append(f"pada {hospitalization}")
    elif admission:
        parts.append(f"mulai {admission}")

    clinical = [str(item) for item in (*diagnosis, *procedures, *treatments) if item]
    if clinical:
        parts.append("dengan " + ", ".join(dict.fromkeys(clinical)))

    if provider:
        parts.append(f"di {provider}")
    if doctor:
        parts.append(f"dibimbing {doctor}")

    if len(parts) < 2:
        # Not enough grounded material for a narrative. Say so instead of
        # producing a sentence that sounds complete but says nothing.
        return {
            "reason_of_visit": None,
            "reason_status": "unknown",
            "reason_note": (
                "Belum ada fakta yang cukup untuk menyusun alasan kunjungan dari "
                "dokumen."
            ),
        }

    return {
        "reason_of_visit": " ".join(parts) + ".",
        "reason_status": "derived",
    }



# ---------------------------------------------------------------------------
# Record projections
# ---------------------------------------------------------------------------

_RECORD_KEYS: tuple[str, ...] = (
    "medication", "procedure", "treatment", "laboratory_result",
    "dosage", "frequency", "duration", "invoice_number", "invoice_date",
    "subtotal", "tax", "total_amount", "currency", "diagnosis", "icd_code",
)


def _records(records: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Flatten relationship records, dropping internal grouping bookkeeping."""
    result: list[dict[str, Any]] = []
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        entry = {
            key: record.get(key)
            for key in _RECORD_KEYS
            if key in record and record.get(key) not in (None, "", [])
        }
        if entry:
            result.append(entry)
    return result


def _service_lines(
    claim_data: Mapping[str, Any],
    normalized: Mapping[str, Any],
    invoice_records: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Project invoice records into the form's service-line grid."""
    lines: list[dict[str, Any]] = []
    for record in invoice_records or []:
        if not isinstance(record, Mapping):
            continue
        line = {
            "service_date_from": _value(claim_data, normalized, "service_date_from"),
            "service_date_to": _value(claim_data, normalized, "service_date_to"),
            "no_of_days": _first(
                _length_of_stay(
                    _value(claim_data, normalized, "service_date_from"),
                    _value(claim_data, normalized, "service_date_to"),
                ),
                0,
            ),
            "service_procedure_code": _raw(claim_data, "service_procedure_code"),
            "service_procedure_desc": _raw(claim_data, "service_procedure_desc"),
            "incurred_amount": _first(
                _value(claim_data, normalized, "total_amount"),
                record.get("total_amount"),
            ),
            "invoice_number": record.get("invoice_number"),
        }
        lines.append({key: value for key, value in line.items() if value is not None})
    if lines:
        return lines
    # No invoice record: still surface a single line when an amount exists.
    amount = _first(
        _value(claim_data, normalized, "amount_incurred"),
        _value(claim_data, normalized, "total_amount"),
    )
    if amount is None:
        return []
    line = {
        "no_of_days": 0,
        "service_procedure_code": _raw(claim_data, "service_procedure_code"),
        "service_procedure_desc": _raw(claim_data, "service_procedure_desc"),
        "incurred_amount": amount,
    }
    return [{key: value for key, value in line.items() if value is not None}]



# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _member_block(claim_data: Mapping[str, Any], normalized: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "insured_name": _first(
            _raw(claim_data, "patient_name"),
            _raw(claim_data, "policy_holder_name"),
        ),
        "dob": _value(claim_data, normalized, "date_of_birth"),
        "patient_id": _raw(claim_data, "patient_id"),
        "member_number": _raw(claim_data, "member_number"),
        "client_no": _raw(claim_data, "client_no"),
        "email": _raw(claim_data, "email"),
        "telephone": _raw(claim_data, "telephone"),
        "mobile_phone": _raw(claim_data, "mobile_phone"),
        "address": _raw(claim_data, "address"),
        "medical_record_number": _raw(claim_data, "medical_record_number"),
        "payee_type": _raw(claim_data, "payee_type"),
    }


def _policy_block(claim_data: Mapping[str, Any], normalized: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_holder_name": _raw(claim_data, "policy_holder_name"),
        "policy_number": _raw(claim_data, "policy_number"),
        "effective_date": _value(claim_data, normalized, "policy_effective_date"),
        "policy_age": _raw(claim_data, "policy_age"),
        "agent_no": _raw(claim_data, "agent_no"),
    }


def _claim_block(
    claim_data: Mapping[str, Any],
    normalized: Mapping[str, Any],
    analyst_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "claim_number": _raw(claim_data, "claim_number"),
        "sub_claim_no": _raw(claim_data, "sub_claim_no"),
        "case_no": _raw(claim_data, "case_no"),
        "precert_no": _raw(claim_data, "precert_no"),
        "claim_case_type": (analyst_analysis.get("claim_case_type") or {}).get("value"),
        "claim_type_label": _raw(claim_data, "claim_type"),
        "account_no": _raw(claim_data, "account_no"),
        "account_name": _raw(claim_data, "account_name"),
        "bank_name": _raw(claim_data, "bank_name"),
        "branch_name": _raw(claim_data, "branch_name"),
        "received_date": _value(claim_data, normalized, "received_date"),
        "accident_date": _value(claim_data, normalized, "accident_date"),
    }


def _provider_block(claim_data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider_name": _raw(claim_data, "provider_name"),
        "provider_code": _raw(claim_data, "provider_code"),
        "doctor_names": list(claim_data.get("doctor_names") or []),
        "service_procedure_code": _raw(claim_data, "service_procedure_code"),
        "service_procedure_desc": _raw(claim_data, "service_procedure_desc"),
    }



def _medical_block(
    claim_data: Mapping[str, Any],
    analyst_analysis: Mapping[str, Any],
    reason: Mapping[str, Any],
) -> dict[str, Any]:
    icd_codes = list(claim_data.get("icd_codes") or [])
    block = {
        "reason_of_visit": reason["reason_of_visit"],
        "reason_status": reason["reason_status"],
        "icd_codes": icd_codes,
        "icd_recommendations": list(analyst_analysis.get("icd_suggestions") or []),
        "diagnosis": list(claim_data.get("diagnosis") or []),
        "medical_history": list(claim_data.get("medical_history") or []),
        "pre_existing_conditions": list(claim_data.get("pre_existing_conditions") or []),
    }
    if reason.get("reason_note"):
        block["reason_note"] = reason["reason_note"]
    return block


def _financial_block(
    claim_data: Mapping[str, Any],
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    currency = _value(claim_data, normalized, "currency")
    currency_is_default = currency is None
    exchange_rate = _value(claim_data, normalized, "exchange_rate")
    exchange_rate_is_default = exchange_rate is None
    return {
        "amount_incurred": _first(
            _value(claim_data, normalized, "amount_incurred"),
            _value(claim_data, normalized, "total_amount"),
        ),
        "currency": DEFAULT_CURRENCY if currency_is_default else currency,
        "currency_status": "default" if currency_is_default else "extracted",
        "exchange_rate": (
            DEFAULT_EXCHANGE_RATE if exchange_rate_is_default else exchange_rate
        ),
        "exchange_rate_status": (
            "default" if exchange_rate_is_default else "extracted"
        ),
        "exchange_rate_date": _value(claim_data, normalized, "exchange_rate_date"),
        "subtotal": _value(claim_data, normalized, "subtotal"),
        "tax": _value(claim_data, normalized, "tax"),
        "total_amount": _value(claim_data, normalized, "total_amount"),
        "other_insurance_amount": _value(
            claim_data, normalized, "other_insurance_amount"
        ),
        "discount_amount": _value(claim_data, normalized, "discount_amount"),
    }



def build_claim_form(
    contract: Mapping[str, Any],
    *,
    pages: Sequence[Mapping[str, Any]] | None = None,
    classifications: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project an auditable claim contract into the compact form JSON.

    ``pages`` is the parser's per-page OCR inventory. ``classifications`` is the
    model-produced document type for each page (see
    ``services.page_classifier``) and supplies both the document inventory and
    the document-level evidence used to separate inpatient from outpatient.
    """
    claim_data = contract.get("claim_data") or {}
    normalized = contract.get("normalized_claim_data") or {}
    analyst_analysis = contract.get("analyst_analysis") or {}
    validation = contract.get("validation") or {}

    visit_block = _visit_block(
        claim_data, normalized, analyst_analysis, pages, classifications
    )
    reason = _reason_of_visit(claim_data, visit_block)
    medical = _medical_block(claim_data, analyst_analysis, reason)
    document_block = _page_inventory(pages, classifications)

    needs_attention = list(validation.get("inconsistencies") or [])
    if not medical["icd_codes"] and medical["diagnosis"]:
        needs_attention.append(
            "Kode ICD tidak tertulis di dokumen; lihat icd_recommendations."
        )
    if visit_block.get("type_of_visit") == "unknown":
        needs_attention.append("Tipe kunjungan belum dapat ditentukan.")

    return {
        "schema_version": CLAIM_FORM_VERSION,
        "document": document_block,
        "member_info": _member_block(claim_data, normalized),
        "policy_info": _policy_block(claim_data, normalized),
        "claim": _claim_block(claim_data, normalized, analyst_analysis),
        "visit": visit_block,
        "provider": _provider_block(claim_data),
        "medical": medical,
        "financial": _financial_block(claim_data, normalized),
        "service_lines": _service_lines(
            claim_data, normalized, claim_data.get("invoice_records")
        ),
        "medications": _records(claim_data.get("medication_records")),
        "procedures": _records(claim_data.get("procedure_records")),
        "lab_results": _records(claim_data.get("lab_results")),
        "review": {
            "missing_fields": list(analyst_analysis.get("missing_fields") or []),
            "missing_information": list(
                analyst_analysis.get("missing_information") or []
            ),
            "needs_attention": needs_attention,
        },
    }


__all__ = [
    "CLAIM_FORM_VERSION",
    "DEFAULT_CURRENCY",
    "DEFAULT_EXCHANGE_RATE",
    "build_claim_form",
]

