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

import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from services.assessment_service import derive_claim_analysis
from services.claim_rules import (
    document_title,
    visit_type_from_documents,
)

CLAIM_FORM_VERSION = "claim-form-v1"
CLAIM_GROUPED_VERSION = "claim-full-v2"
DEFAULT_CURRENCY = "IDR"
DEFAULT_EXCHANGE_RATE = 1

# Key JSON grouped = ``name`` di editForm agar autofill $('#name') 1:1.
# Value = kandidat field kontrak (snake_case) berurutan prioritas.
FORM_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "insuredName": ("patient_name", "policy_holder_name"),
    "policyHolder": ("policy_holder_name",),
    "clientNo": ("client_no", "member_number", "patient_id"),
    "dob": ("date_of_birth",),
    "mobilePhone": ("mobile_phone",),
    "email": ("email",),
    "telephone": ("telephone",),
    "address": ("address",),
    "sysvClaimType": ("claim_type",),
    "sysvPayeeType": ("payee_type",),
    "hphpName": ("provider_name",),
    "hphpId": ("provider_code",),
    "reasonOfVists": ("reason_of_visit",),
    "icdCode1": ("icd_code_1",),
    "icdCode2": ("icd_code_2",),
    "visitFrmDtm": ("admission_date", "hospitalization_date"),
    "visitToDtm": ("discharge_date", "hospitalization_date"),
    "noOfDays": ("no_of_days",),
    "incuredAmt": ("total_amount", "amount_incurred"),
    "sysvCurrencyType": ("currency",),
    "exchangeRate": ("exchange_rate",),
    "exchangeRateDtm": ("exchange_rate_date",),
    "subClaimNo": ("sub_claim_no",),
    "caseNo": ("case_no",),
    "typeOfVisit": ("visit_type",),
}


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


def apply_document_claim_type(
    contract: Mapping[str, Any],
    pages: Sequence[Mapping[str, Any]] | None,
    classifications: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Derive claim stage from named document pages when the inventory permits it."""
    page_classifications = {
        str(item.get("page")): item
        for item in classifications or []
        if isinstance(item, Mapping) and item.get("page") is not None
    }
    page_documents: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for page in pages or []:
        classification = page_classifications.get(str(page.get("page_number")))
        if classification is not None:
            page_documents.append((page, classification))

    def is_named(classification: Mapping[str, Any]) -> bool:
        return bool(str(classification.get("document_type") or "").strip()) and (
            classification.get("source") != "not_named"
        )

    def is_new_document(classification: Mapping[str, Any]) -> bool:
        document_type = " ".join(
            str(classification.get(key) or "")
            for key in ("document_type", "label", "title")
        ).lower()
        return (
            classification.get("category") == "formulir_klaim"
            or re.search(r"\bskd\b", document_type) is not None
            or "surat keterangan dokter" in document_type
            or "formulir" in document_type
        )

    new_documents = [
        (page, classification)
        for page, classification in page_documents
        if is_new_document(classification)
        and (
            is_named(classification)
            or classification.get("category") == "formulir_klaim"
        )
    ]
    inventory_complete = bool(pages) and len(page_documents) == len(pages) and all(
        is_named(classification) for _, classification in page_documents
    )

    analysis = dict(contract.get("analyst_analysis") or {})
    current_claim_case = dict(analysis.get("claim_case_type") or {})
    current_value = current_claim_case.get("value")
    if new_documents:
        claim_value = "new"
        evidence = [
            str(classification.get("document_type") or classification.get("category"))
            for _, classification in new_documents
        ]
        evidence_refs = [str(page.get("page_number")) for page, _ in new_documents]
        reason = "Dokumen SKD atau formulir klaim ditemukan; kategori klaim adalah new."
    elif inventory_complete and current_value != "new":
        claim_value = "completeness"
        evidence = [
            str(classification.get("document_type"))
            for _, classification in page_documents
        ]
        evidence_refs = [str(page.get("page_number")) for page, _ in page_documents]
        reason = "Semua halaman terklasifikasi dan formulir klaim tidak ditemukan."
    else:
        return dict(contract)

    method = "claim_document_inventory"
    claim_case = {
        **current_claim_case,
        "value": claim_value,
        "status": "derived",
        "confidence": None,
        "confidence_type": "grounded_rule",
        "method": method,
        "reason": reason,
        "evidence": evidence,
        "evidence_refs": evidence_refs,
        "note": reason,
    }
    analysis["claim_case_type"] = claim_case

    decisions = dict(analysis.get("decisions") or {})
    for name, value in (
        ("claimType", claim_value),
        ("isNewClaim", claim_value == "new"),
        ("isKelengkapan", claim_value == "completeness"),
    ):
        decisions[name] = {
            "value": value,
            "reason": reason,
            "evidence": evidence,
            "method": method,
            "confidence": None,
        }
    analysis["decisions"] = decisions

    updated = dict(contract)
    updated["analyst_analysis"] = analysis
    return updated


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
    contract = apply_document_claim_type(contract, pages, classifications)
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


def _is_empty_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _prune_parent(
    mapping: Mapping[str, Any], *, ignore: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """Hapus parent bila semua value kosong atau hanya default semu.

    ``ignore``: pasangan key->default yang dianggap sama dengan kosong
    untuk pruning (mis. noOfDays=0, currency IDR default). Bila parent
    dipertahankan, value asli tetap disimpan.
    """
    ignore = ignore or {}
    cleaned: dict[str, Any] = {}
    meaningful = False
    for key, value in mapping.items():
        if _is_empty_value(value):
            continue
        if key in ignore and value == ignore[key]:
            cleaned[key] = value
            continue
        cleaned[key] = value
        meaningful = True
    if not meaningful:
        return None
    return cleaned


def _pick(
    claim_data: Mapping[str, Any], normalized: Mapping[str, Any], *keys: str
) -> Any:
    for key in keys:
        value = _value(claim_data, normalized, key)
        if value not in (None, "", [], {}):
            return value
    return None


def _icd_at(claim_data: Mapping[str, Any], index: int) -> Any:
    codes = list(claim_data.get("icd_codes") or [])
    if index < len(codes):
        return codes[index]
    return None


def _decisions_from_analysis(
    analyst_analysis: Mapping[str, Any],
) -> dict[str, Any]:
    decisions = dict(analyst_analysis.get("decisions") or {})
    if decisions:
        return decisions
    visit = analyst_analysis.get("visit_type") or {}
    claim_case = analyst_analysis.get("claim_case_type") or {}
    out: dict[str, Any] = {}
    if visit:
        out["visitType"] = {
            "value": visit.get("value", "unknown"),
            "reason": "; ".join(visit.get("evidence") or []),
            "evidence": list(visit.get("evidence") or []),
            "method": visit.get("method") or "",
            "confidence": visit.get("confidence"),
        }
    if claim_case:
        out["claimType"] = {
            "value": claim_case.get("value", "unknown"),
            "reason": "; ".join(claim_case.get("evidence") or []),
            "evidence": list(claim_case.get("evidence") or []),
            "method": claim_case.get("method") or "",
            "confidence": claim_case.get("confidence"),
        }
    return out


def build_grouped_claim_form(
    contract: Mapping[str, Any],
    *,
    pages: Sequence[Mapping[str, Any]] | None = None,
    classifications: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Grouped slim JSON (claim-full-v2) pakai nama editForm + usage.

    Hanya field penting per perawatan. Parent yang kosong total dihapus
    (sesuai: yang ngga masuk gausah ada parentnya). Usage LLM ikut bila
    tersedia di kontrak (key ``usage``).
    """
    contract = apply_document_claim_type(contract, pages, classifications)
    claim_data = contract.get("claim_data") or {}
    normalized = contract.get("normalized_claim_data") or {}
    analyst_analysis = contract.get("analyst_analysis") or {}
    provenance = contract.get("provenance") or {}
    validation = contract.get("validation") or {}

    visit_block = _visit_block(
        claim_data, normalized, analyst_analysis, pages, classifications
    )
    reason = _reason_of_visit(claim_data, visit_block)
    financial = _financial_block(claim_data, normalized)
    visit_value = visit_block.get("type_of_visit") or "unknown"
    claim_value = (analyst_analysis.get("claim_case_type") or {}).get(
        "value", "unknown"
    )
    signals = analyst_analysis.get("claim_signals") or {}
    decisions = _decisions_from_analysis(analyst_analysis)

    def decision_flag(name: str, fallback: bool) -> bool:
        decision = decisions.get(name)
        if isinstance(decision, Mapping) and isinstance(decision.get("value"), bool):
            return decision["value"]
        return bool(signals.get(name, fallback))

    is_ri = decision_flag("isRI", visit_value == "inpatient")
    is_rj = decision_flag("isRJ", visit_value == "outpatient")
    visit_conf = (analyst_analysis.get("visit_type") or {}).get("confidence")

    grouped: dict[str, Any] = {"schema_version": CLAIM_GROUPED_VERSION}
    grouped["isNewClaim"] = decision_flag("isNewClaim", claim_value == "new")
    grouped["isKelengkapan"] = bool(
        decision_flag("isKelengkapan", claim_value == "completeness")
    )
    grouped["isRI"] = is_ri
    grouped["isRJ"] = is_rj
    grouped["isOverseas"] = decision_flag("isOverseas", False)
    grouped["possibleNewClaim"] = decision_flag("possibleNewClaim", False)
    if signals.get("riConfidence") is not None:
        grouped["riConfidence"] = signals.get("riConfidence")
    elif is_ri:
        grouped["riConfidence"] = visit_conf
    if signals.get("rjConfidence") is not None:
        grouped["rjConfidence"] = signals.get("rjConfidence")
    elif is_rj:
        grouped["rjConfidence"] = visit_conf
    if decisions:
        grouped["decisions"] = decisions

    member = _prune_parent({
        # MEMBER INFO (screenshot 1): INSURED NAME, DOB, CLIENT NO, EMAIL,
        # TELEPHONE, MOBILE PHONE, ADDRESS, CLAIM TYPE, PAYEE TYPE
        "insuredName": _pick(claim_data, normalized, "patient_name", "policy_holder_name"),
        "dob": _value(claim_data, normalized, "date_of_birth"),
        "clientNo": _pick(claim_data, normalized, "client_no", "member_number", "patient_id"),
        "email": _raw(claim_data, "email"),
        "telephone": _raw(claim_data, "telephone"),
        "mobilePhone": _raw(claim_data, "mobile_phone"),
        "address": _raw(claim_data, "address"),
        "sysvClaimType": _raw(claim_data, "claim_type"),
        "sysvPayeeType": _raw(claim_data, "payee_type"),
        "similarityScore": (analyst_analysis.get("name_check") or {}).get(
            "insured_similarity_pct"
        ),
    })
    if member is not None:
        grouped["memberInfo"] = member

    doctors = list(claim_data.get("doctor_names") or [])
    if claim_data.get("doctor_name") and claim_data["doctor_name"] not in doctors:
        doctors = [claim_data["doctor_name"], *doctors]
    header_raw = {
        # HEADER (screenshot 2): kolom kiri + tengah + kanan form editForm.
        # Hanya field penting sesuai perawatan; refund/bypass/source/fast/
        # moreVisit tetap di full contract bila dibutuhkan audit.
        "accountNo": _raw(claim_data, "account_no"),
        "accountName": _raw(claim_data, "account_name"),
        "bankIdDesc": _raw(claim_data, "bank_name"),
        "bankBranchName": _raw(claim_data, "branch_name"),
        "receivedDtm": _value(claim_data, normalized, "received_date"),
        "submitDtm": _value(claim_data, normalized, "submit_date"),
        "accidentDtm": _value(claim_data, normalized, "accident_date"),
        "typeOfVisit": visit_block.get("type_of_visit"),
        "visitFrmDtm": visit_block.get("admission_date")
        or visit_block.get("hospitalization_date"),
        "visitToDtm": visit_block.get("discharge_date")
        or visit_block.get("hospitalization_date"),
        "noOfDays": visit_block.get("no_of_days") or 0,
        "hphpName": _raw(claim_data, "provider_name"),
        "hphpId": _raw(claim_data, "provider_code"),
        "hpspNames": doctors,
        "reasonOfVists": reason.get("reason_of_visit"),
        "icdCode1": _icd_at(claim_data, 0),
        "icdCode2": _icd_at(claim_data, 1),
        "subClaimNo": _raw(claim_data, "sub_claim_no"),
        "caseNo": _raw(claim_data, "case_no"),
        "preCertNo": _raw(claim_data, "precert_no"),
        "sysvCurrencyType": financial.get("currency"),
        "exchangeRate": financial.get("exchange_rate"),
        "exchangeRateDtm": financial.get("exchange_rate_date"),
        "invoiceNo": _raw(claim_data, "invoice_number"),
        "discountAmt": _value(claim_data, normalized, "discount_amount"),
        "cobAmt": _value(claim_data, normalized, "other_insurance_amount"),
        "incuredAmt": financial.get("amount_incurred"),
        "incuredIdrAmt": financial.get("amount_incurred"),
    }
    # typeOfVisit=unknown + noOfDays=0 + currency default (IDR/1) dianggap
    # semu: header hilang bila tidak ada fakta header lain.
    header = _prune_parent(
        header_raw,
        ignore={
            "typeOfVisit": "unknown",
            "noOfDays": 0,
            "sysvCurrencyType": DEFAULT_CURRENCY,
            "exchangeRate": DEFAULT_EXCHANGE_RATE,
        },
    )
    if header is not None:
        grouped["header"] = header

    policy = _prune_parent({
        # POLICY (screenshot 3 atas): POLICY HOLDER, EFFECTIVE DATE/RIDER,
        # REINSTATE, POLICY/RIDER AGE, AGENT. Hilang bila semua null.
        "policyHolder": _raw(claim_data, "policy_holder_name"),
        "effectiveDate": _value(claim_data, normalized, "policy_effective_date"),
        "policyAge": _raw(claim_data, "policy_age"),
        "agentNo": _raw(claim_data, "agent_no"),
    })
    if policy is not None:
        grouped["policyInfo"] = policy

    slim_lines = []
    for line in _service_lines(
        claim_data, normalized, claim_data.get("invoice_records")
    ):
        slim_lines.append({
            "serviceDateFrom": line.get("service_date_from"),
            "serviceDateTo": line.get("service_date_to"),
            "spspId": line.get("service_procedure_code")
            or line.get("service_procedure_desc"),
            "incuredAmt": line.get("incurred_amount"),
        })
    if slim_lines:
        grouped["serviceLines"] = slim_lines

    field_evidence = provenance.get("field_evidence") or {}
    slim_evidence = {
        key: field_evidence.get(key)
        for key in (
            "patient_name", "provider_name", "total_amount",
            "admission_date", "discharge_date",
        )
        if field_evidence.get(key)
    }
    if slim_evidence:
        grouped["provenance"] = {"fieldEvidence": slim_evidence}
    review = {
        "missingFields": list(analyst_analysis.get("missing_fields") or []),
        "needsAttention": list(validation.get("inconsistencies") or [])
        + list(analyst_analysis.get("inconsistencies") or []),
    }
    if any(review.values()):
        grouped["review"] = review

    usage = contract.get("usage")
    if isinstance(usage, Mapping) and usage:
        grouped["usage"] = dict(usage)
    return grouped


def build_full_claim_json(
    contract: Mapping[str, Any],
    *,
    pages: Sequence[Mapping[str, Any]] | None = None,
    classifications: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Full JSON slim: grouped + decisions + provenance ringkas + usage."""
    return build_grouped_claim_form(
        contract, pages=pages, classifications=classifications
    )


def refresh_grouped_decisions(
    contract: Mapping[str, Any], grouped: Mapping[str, Any]
) -> dict[str, Any]:
    """Refresh decision objects in persisted grouped JSON from contract facts."""
    provenance = contract.get("provenance") or {}
    analyst_analysis = contract.get("analyst_analysis") or {}
    refreshed_analysis = derive_claim_analysis(
        contract.get("claim_data") or {},
        contract.get("normalized_claim_data") or {},
        provenance.get("field_evidence") or {},
        contract.get("validation") or {},
        icd_suggestions=analyst_analysis.get("icd_suggestions"),
    )
    refreshed = dict(grouped)
    decisions = refreshed_analysis["decisions"]
    refreshed["decisions"] = decisions
    for decision_name, root_name in (
        ("isRI", "isRI"),
        ("isRJ", "isRJ"),
        ("isNewClaim", "isNewClaim"),
        ("isKelengkapan", "isKelengkapan"),
        ("isOverseas", "isOverseas"),
        ("possibleNewClaim", "possibleNewClaim"),
    ):
        value = decisions[decision_name]["value"]
        refreshed[root_name] = value if isinstance(value, bool) else False
    return refreshed


__all__ = [
    "CLAIM_FORM_VERSION",
    "CLAIM_GROUPED_VERSION",
    "DEFAULT_CURRENCY",
    "DEFAULT_EXCHANGE_RATE",
    "FORM_NAME_ALIASES",
    "apply_document_claim_type",
    "build_claim_form",
    "build_full_claim_json",
    "build_grouped_claim_form",
    "refresh_grouped_decisions",
]

