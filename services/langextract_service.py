"""LangExtract stage for Markdown produced by the document/VLM stage.

The VLM stage is deliberately outside this module.  LangExtract receives the
complete Markdown string, extracts small grounded facts, and never receives a
page image.  Python then builds the claim contract from those facts.

Design rules implemented here:

* extraction_text is the verbatim source span;
* char offsets and the source excerpt are retained for every grounded item;
* ungrounded model output is diagnostic only and is not promoted to claim_data;
* model-provided group IDs are treated as hints and replaced with deterministic
  occurrence IDs;
* derived analysis is built separately from explicit claim facts;
* output_schema constrains LangExtract's raw envelope, not the final business
  JSON contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import textwrap
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from config import AppConfig
from services.claim_fields import (
    claim_fields,
    date_fields,
    described_fields,
    field_descriptions,
    family_by_field,
    group_definitions,
    group_record_keys,
    group_types,
    list_destinations,
    list_fields,
    numeric_fields,
    scalar_fields,
    undescribed_fields,
)
from services.claim_rules import canonical_claim_case_type, canonical_visit_type
from services.usage_tracker import UsageLedger, build_usage_report

try:  # Keep the rest of the app importable when the optional extra is absent.
    import langextract as lx
except ImportError:  # pragma: no cover - exercised only in a missing dependency env
    lx = None  # type: ignore[assignment]


SCHEMA_VERSION = "claim-fact-v1"
LANGEXTRACT_CLASS = "claim_fact"
ICD_SUGGESTION_CLASS = "icd_suggestion"

# Derived from services/claim_fields.FIELD_SPECS.
CLAIM_FIELDS: tuple[str, ...] = claim_fields()
SCALAR_FIELDS: tuple[str, ...] = scalar_fields()
DATE_FIELDS = date_fields()
NUMERIC_FIELDS = numeric_fields()
LIST_FIELDS: tuple[str, ...] = list_fields()
GROUP_TYPES: tuple[str, ...] = group_types()
FAMILY_BY_FIELD: dict[str, str] = family_by_field()
LIST_DESTINATIONS: dict[str, str] = list_destinations()


ANCHOR_FIELDS = frozenset({"diagnosis", "medication", "invoice_number", "laboratory_result", "procedure", "supporting_document"})

_PAGE_MARKER_PATTERNS = (
    re.compile(r"<!--\s*page(?:[ _-]?number)?\s*[:=#-]?\s*(\d+)\s*-->", re.IGNORECASE),
    re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:page|halaman)\s*(?:number|no\.?)?\s*[:=#-]?\s*(\d+)\s*$",
        re.IGNORECASE | re.MULTILINE,
    ),
)


class LangExtractUnavailableError(RuntimeError):
    """Raised when the LangExtract dependency cannot be imported."""


class LangExtractConfigurationError(ValueError):
    """Raised when the selected provider cannot honor the requested mode."""


def _require_langextract() -> Any:
    if lx is None:
        raise LangExtractUnavailableError(
            "LangExtract belum terpasang. Jalankan `python -m pip install -r requirements.txt`."
        )
    return lx


def _text_attribute(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value if item is not None)
    return str(value)


def _enum_text(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "value", str(value))


def _value_from_item(item: Mapping[str, Any]) -> str:
    """Use the source span as the business value whenever it is available."""
    source_value = item.get("source_value")
    if source_value not in (None, ""):
        return str(source_value)
    return str(item.get("extraction_text", ""))


def _page_markers(markdown: str) -> list[tuple[int, int]]:
    markers: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in _PAGE_MARKER_PATTERNS:
        for match in pattern.finditer(markdown):
            span = (match.start(), match.end())
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            try:
                page_number = int(match.group(1))
            except (TypeError, ValueError):
                continue
            markers.append((span[0], page_number))
            occupied.append(span)
    return sorted(markers)


def _page_for_offset(markers: Sequence[tuple[int, int]], offset: int | None) -> int | None:
    if offset is None or not markers:
        return None
    page_number: int | None = None
    for marker_offset, candidate in markers:
        if marker_offset > offset:
            break
        page_number = candidate
    return page_number


def _field_default_group_type(field_name: str) -> str:
    family = FAMILY_BY_FIELD.get(field_name, "none")
    if family in {"identity", "provider", "claim", "document"}:
        return family
    return family


def _attribute_dict(extraction: Any) -> dict[str, str]:
    attributes = getattr(extraction, "attributes", None) or {}
    if not isinstance(attributes, Mapping):
        return {}
    return {str(key): _text_attribute(value) for key, value in attributes.items()}


def _item_from_extraction(
    extraction: Any,
    *,
    markdown: str,
    document_id: str | None,
    page_markers: Sequence[tuple[int, int]],
) -> dict[str, Any]:
    extraction_text = str(getattr(extraction, "extraction_text", "") or "")
    attributes = _attribute_dict(extraction)
    field_name = attributes.get("field_name", "").strip()
    if field_name not in CLAIM_FIELDS:
        field_name = ""
    model_group_id = attributes.get("group_id", "").strip()
    group_type = attributes.get("group_type", "").strip() or _field_default_group_type(field_name)
    if group_type not in GROUP_TYPES:
        group_type = _field_default_group_type(field_name)
    role = attributes.get("role", "value").strip() or "value"

    interval = getattr(extraction, "char_interval", None)
    start = getattr(interval, "start_pos", None) if interval is not None else None
    end = getattr(interval, "end_pos", None) if interval is not None else None
    valid_interval = (
        isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(markdown)
    )
    source_value = markdown[start:end] if valid_interval else None
    alignment_status = _enum_text(getattr(extraction, "alignment_status", None))
    grounding_status = "grounded" if valid_interval and source_value is not None else "ungrounded"
    exact_match = bool(valid_interval and source_value == extraction_text)
    if valid_interval and source_value is not None and not exact_match:
        # Keep the model's text for audit, but never use it as the business
        # value when LangExtract aligned a fuzzy/non-identical span.
        source_value = source_value
    page_number = _page_for_offset(page_markers, start if valid_interval else None)
    alignment_key = _enum_text(getattr(extraction, "alignment_status", None)) or "unknown"
    identity_source = "|".join(
        [
            str(document_id or "document"),
            LANGEXTRACT_CLASS,
            field_name or "invalid",
            str(start if valid_interval else ""),
            str(end if valid_interval else ""),
            extraction_text,
            model_group_id,
        ]
    )
    extraction_id = hashlib.sha1(identity_source.encode("utf-8")).hexdigest()[:16]

    return {
        "extraction_id": extraction_id,
        "document_id": document_id,
        "extraction_class": LANGEXTRACT_CLASS,
        "field_name": field_name,
        "extraction_text": extraction_text,
        "source_value": source_value,
        "source_text": source_value,
        "attributes": attributes,
        "model_group_id": model_group_id,
        "group_type": group_type,
        "role": role,
        "fact_status": "explicit",
        "char_start": start if valid_interval else None,
        "char_end": end if valid_interval else None,
        "source_page": page_number,
        "alignment_status": alignment_key,
        "grounding_status": grounding_status,
        "exact_match": exact_match,
    }


def _annotated_documents(result: Any) -> list[Any]:
    if result is None:
        return []
    if isinstance(result, (list, tuple)):
        return list(result)
    if hasattr(result, "extractions"):
        return [result]
    return []


def serialize_langextract_result(
    result: Any,
    *,
    markdown: str,
) -> dict[str, Any]:
    """Convert LangExtract dataclasses into a portable, auditable JSON shape."""
    page_markers = _page_markers(markdown)
    items: list[dict[str, Any]] = []
    invalid_items: list[dict[str, Any]] = []
    for document_index, document in enumerate(_annotated_documents(result)):
        document_id = getattr(document, "document_id", None)
        if document_id is None:
            document_id = f"document-{document_index + 1}"
        for extraction in getattr(document, "extractions", None) or []:
            item = _item_from_extraction(
                extraction,
                markdown=markdown,
                document_id=str(document_id),
                page_markers=page_markers,
            )
            if item["field_name"]:
                items.append(item)
            else:
                invalid_items.append(item)

    return {
        "schema_version": SCHEMA_VERSION,
        "items": items,
        "invalid_items": invalid_items,
        "source_characters": len(markdown),
        "page_markers": [
            {"char_start": offset, "page": page} for offset, page in page_markers
        ],
    }


def _repair_missing_group_ids(items: list[dict[str, Any]]) -> None:
    """Use nearby anchors when a provider omits a relationship attribute."""
    last_anchor: dict[str, str] = {}
    sorted_items = sorted(
        items,
        key=lambda item: (
            item.get("char_start") if item.get("char_start") is not None else math.inf,
            item.get("char_end") if item.get("char_end") is not None else math.inf,
        ),
    )
    for item in sorted_items:
        field_name = item.get("field_name", "")
        family = FAMILY_BY_FIELD.get(field_name, "none")
        group_id = str(item.get("model_group_id") or "").strip()
        if not group_id or group_id.lower() in {"none", "unknown", "null", "n/a"}:
            group_id = last_anchor.get(family, "")
        if not group_id:
            group_id = f"{family or 'group'}_{field_name or 'fact'}"
        item["group_id"] = group_id
        if field_name in ANCHOR_FIELDS:
            last_anchor[family] = group_id


def _assign_canonical_groups(items: list[dict[str, Any]]) -> None:
    """Convert model group hints into stable occurrence IDs.

    A repeated value at another character range is a different occurrence and
    is intentionally retained.  A group is split when the same model hint
    reappears after a large gap or after another group of the same family.
    """
    _repair_missing_group_ids(items)
    counters: Counter[str] = Counter()
    last_group_by_family: dict[str, str] = {}
    last_end_by_family: dict[str, int] = {}
    canonical_by_key: dict[tuple[str, str], str] = {}
    seen_raw_by_family: set[tuple[str, str]] = set()
    sorted_items = sorted(
        items,
        key=lambda item: (
            item.get("char_start") if item.get("char_start") is not None else math.inf,
            item.get("field_name", ""),
        ),
    )
    for item in sorted_items:
        family = FAMILY_BY_FIELD.get(item.get("field_name", ""), "none")
        raw_group = str(item.get("group_id", "") or f"{family}_unknown")
        if family in {"identity", "claim", "provider", "document"}:
            canonical = f"{family}_{raw_group}"
            item["canonical_group_id"] = canonical
            continue

        start = item.get("char_start")
        end = item.get("char_end")
        previous_group = last_group_by_family.get(family)
        previous_end = last_end_by_family.get(family)
        should_split = False
        if previous_group != raw_group:
            should_split = (family, raw_group) in seen_raw_by_family
        elif (
            isinstance(start, int)
            and isinstance(previous_end, int)
            and start - previous_end > 2500
        ):
            should_split = True
        if should_split:
            key = (family, f"{raw_group}#{counters[(family, raw_group)]}")
        else:
            key = (family, raw_group)
        if key not in canonical_by_key:
            counters[family] += 1
            canonical_by_key[key] = f"{family}_{counters[family]}"
        seen_raw_by_family.add((family, raw_group))
        canonical = canonical_by_key[key]
        item["canonical_group_id"] = canonical
        last_group_by_family[family] = raw_group
        if isinstance(end, int):
            last_end_by_family[family] = end


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate only identical spans, never identical text occurrences."""
    grounded: dict[tuple[Any, ...], dict[str, Any]] = {}
    ungrounded: list[dict[str, Any]] = []
    for item in items:
        start = item.get("char_start")
        end = item.get("char_end")
        if item.get("grounding_status") == "grounded":
            key = (
                item.get("field_name"),
                start,
                end,
                item.get("source_value"),
                item.get("canonical_group_id"),
            )
            existing = grounded.get(key)
            if existing is None:
                grounded[key] = item
            elif not existing.get("exact_match") and item.get("exact_match"):
                grounded[key] = item
        else:
            ungrounded.append(item)

    result = list(grounded.values()) + ungrounded
    result.sort(
        key=lambda item: (
            item.get("char_start") if item.get("char_start") is not None else math.inf,
            item.get("field_name", ""),
            item.get("extraction_id", ""),
        )
    )
    return result


def prepare_intermediate_items(serialized: Mapping[str, Any]) -> dict[str, Any]:
    items = [dict(item) for item in serialized.get("items", []) if isinstance(item, Mapping)]
    _assign_canonical_groups(items)
    prepared = _deduplicate_items(items)
    return {
        **dict(serialized),
        "items": prepared,
        "grounded_item_count": sum(item.get("grounding_status") == "grounded" for item in prepared),
        "ungrounded_item_count": sum(item.get("grounding_status") != "grounded" for item in prepared),
    }


def _evidence(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "extraction_id": item.get("extraction_id"),
        "raw_value": item.get("extraction_text"),
        "source_text": item.get("source_text"),
        "char_start": item.get("char_start"),
        "char_end": item.get("char_end"),
        "source_page": item.get("source_page"),
        "grounding_status": item.get("grounding_status"),
        "alignment_status": item.get("alignment_status"),
        "exact_match": item.get("exact_match"),
    }


def _item_evidence(items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in items:
        key = (
            item.get("extraction_id"),
            item.get("char_start"),
            item.get("char_end"),
            item.get("source_text"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(_evidence(item))
    return result


def _value_lists(items: Sequence[Mapping[str, Any]], field_name: str) -> list[str]:
    return [
        _value_from_item(item)
        for item in items
        if item.get("field_name") == field_name and _value_from_item(item) != ""
    ]


def _grouped_records(
    items: Sequence[Mapping[str, Any]],
    *,
    family: str,
    fields: Sequence[str],
    anchor_field: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in items:
        item_family = FAMILY_BY_FIELD.get(item.get("field_name", ""), "none")
        if item_family == family and item.get("grounding_status") == "grounded":
            group_id = str(item.get("canonical_group_id") or f"{family}_unknown")
            grouped[group_id].append(item)

    records: list[dict[str, Any]] = []
    for group_id, group_items in sorted(grouped.items()):
        record: dict[str, Any] = {"group_id": group_id, anchor_field: None}
        occurrences: dict[str, list[str]] = {}
        for field_name in fields:
            values = _value_lists(group_items, field_name)
            if values:
                record[field_name] = values[0]
                occurrences[field_name] = values
            else:
                record[field_name] = None
        record["occurrences"] = occurrences
        record["evidence"] = _item_evidence(group_items)
        records.append(record)
    return records


def _empty_claim_data() -> dict[str, Any]:
    """Create the explicit-fact contract skeleton from the field registry."""
    data: dict[str, Any] = {field: None for field in SCALAR_FIELDS}
    # A field may remain explicitly null when absent, even if it is also used
    # to derive a canonical group/analysis value.
    seen: set[str] = set()
    for destination in LIST_DESTINATIONS.values():
        if destination in seen:
            continue
        seen.add(destination)
        data[destination] = []
    for record_key in group_record_keys():
        data.setdefault(record_key, [])
    return data


def _raw_value_for_field(item: Mapping[str, Any]) -> str:
    return _value_from_item(item)


def _parse_amount(raw_value: Any) -> int | float | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"(?i)\b(?:rp|idr|rupiah|usd|us\$|\$|€|eur|gbp)\b", "", text)
    text = text.replace("Rp", "").replace("IDR", "").replace("USD", "")
    text = re.sub(r"[^\d.,\-+]", "", text)
    if not text or not re.search(r"\d", text):
        return None
    if text.count(".") and text.count(","):
        last_dot = text.rfind(".")
        last_comma = text.rfind(",")
        decimal_separator = "." if last_dot > last_comma else ","
        thousands_separator = "," if decimal_separator == "." else "."
        text = text.replace(thousands_separator, "")
        if decimal_separator == ",":
            text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif text.count(".") == 1 and len(text.rsplit(".", 1)[1]) == 3:
        text = text.replace(".", "")
    elif text.count(",") == 1 and len(text.rsplit(",", 1)[1]) == 3:
        text = text.replace(",", "")
    elif text.count(",") == 1:
        text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if negative:
        number = -number
    if number == number.to_integral_value():
        return int(number)
    return float(number)


_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "mei": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "agu": 8,
    "agustus": 8,
    "sep": 9,
    "september": 9,
    "october": 10,
    "okt": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "des": 12,
    "dec": 12,
}


def _parse_date(raw_value: Any) -> str | None:
    if raw_value is None:
        return None
    text = str(raw_value).strip().rstrip(".")
    if not text:
        return None
    iso_candidate = text[:10]
    try:
        return date.fromisoformat(iso_candidate).isoformat()
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return None
    match = re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", text)
    if match:
        month_name, day, year = match.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return None
    return None


def _normalize_currency(raw_value: Any) -> str | None:
    text = str(raw_value or "").strip().lower()
    if not text:
        return None
    # An amount such as "Rp 986.882" is an explicit currency marker, but
    # the currency itself is still a derived normalization rather than a
    # separately extracted document field.
    if (
        text in {"rp", "idr", "rupiah"}
        or "rupiah" in text
        or re.search(r"(?:^|[\s(])rp(?=$|[\s\d(])", text)
        or re.search(r"(?:^|[\s(])idr(?=$|[\s\d(])", text)
    ):
        return "IDR"
    if text in {"usd", "us$", "$"} or re.search(r"(?:^|[\s(])usd(?=$|[\s\d(])", text):
        return "USD"
    if text in {"eur", "€"} or re.search(r"(?:^|[\s(])eur(?=$|[\s\d(])", text):
        return "EUR"
    if text in {"gbp", "£"} or re.search(r"(?:^|[\s(])gbp(?=$|[\s\d(])", text):
        return "GBP"
    return None


def _build_normalized_claim_data(
    claim_data: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    normalized: dict[str, Any] = {}
    notes: list[str] = []
    for field_name in SCALAR_FIELDS:
        raw_value = claim_data.get(field_name)
        if field_name in DATE_FIELDS:
            normalized_value = _parse_date(raw_value)
            if raw_value and normalized_value is None:
                notes.append(f"Could not normalize date field {field_name}: {raw_value}")
        elif field_name in NUMERIC_FIELDS:
            normalized_value = _parse_amount(raw_value)
            if raw_value and normalized_value is None:
                notes.append(f"Could not normalize numeric field {field_name}: {raw_value}")
        elif field_name == "currency":
            normalized_value = _normalize_currency(raw_value)
        else:
            normalized_value = raw_value
        normalized[field_name] = normalized_value

    # Medication flat value list uses historic key `medications`.
    LEGACY_FLAT_LIST_ALIASES = {"medication": "medications", "laboratory_result": "lab_results"}
    for field_name in LIST_FIELDS:
        destination = LIST_DESTINATIONS.get(field_name, field_name)
        normalized.setdefault(destination, [])
        normalized[destination] = list(claim_data.get(destination, []) or [])
    # Older consumers expect these flat-list aliases too.
    for field_name, alias in LEGACY_FLAT_LIST_ALIASES.items():
        destination = LIST_DESTINATIONS.get(field_name, field_name)
        normalized[alias] = list(normalized.get(destination, []) or [])

    admission = normalized.get("admission_date")
    discharge = normalized.get("discharge_date")
    if admission and discharge:
        try:
            normalized["length_of_stay_days"] = (
                date.fromisoformat(discharge) - date.fromisoformat(admission)
            ).days
        except ValueError:
            normalized["length_of_stay_days"] = None
    else:
        normalized["length_of_stay_days"] = None

    # A currency marker attached to an amount is useful for normalization but
    # is not promoted to an explicit currency fact.
    if not normalized.get("currency"):
        for field_name in NUMERIC_FIELDS:
            raw_value = claim_data.get(field_name)
            inferred_currency = _normalize_currency(raw_value)
            if inferred_currency:
                normalized["currency"] = inferred_currency
                notes.append(
                    f"Currency {inferred_currency} was inferred from {field_name} formatting; explicit document currency remains unchanged."
                )
                break
    return normalized, notes


def _field_conflicts(
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for field_name in SCALAR_FIELDS:
        values = []
        for evidence in field_evidence.get(field_name, []):
            value = evidence.get("source_text")
            if value not in (None, "") and value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts.append(
                {
                    "field": field_name,
                    "values": values,
                    "type": "multiple_explicit_values",
                }
            )
    return conflicts


def _validate_claim(
    claim_data: Mapping[str, Any],
    normalized: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    conflicts = _field_conflicts(claim_data, field_evidence)
    inconsistencies: list[str] = []
    admission = normalized.get("admission_date")
    discharge = normalized.get("discharge_date")
    if admission and discharge:
        try:
            if date.fromisoformat(discharge) < date.fromisoformat(admission):
                inconsistencies.append("Discharge date is earlier than admission date.")
        except ValueError:
            inconsistencies.append("Admission or discharge date could not be validated.")

    subtotal = normalized.get("subtotal")
    tax = normalized.get("tax")
    total = normalized.get("total_amount")
    if all(isinstance(value, (int, float)) for value in (subtotal, tax, total)):
        if abs(float(subtotal) + float(tax) - float(total)) > 0.01:
            inconsistencies.append("Subtotal + tax does not equal total amount.")

    grounded = [item for item in items if item.get("grounding_status") == "grounded"]
    ungrounded = [item for item in items if item.get("grounding_status") != "grounded"]
    warnings: list[str] = []
    if ungrounded:
        warnings.append(
            f"{len(ungrounded)} LangExtract item(s) had no source interval and were excluded from claim_data."
        )
    if conflicts:
        warnings.append("One or more scalar fields contain multiple explicit values.")
    return {
        "grounded_item_count": len(grounded),
        "ungrounded_item_count": len(ungrounded),
        "source_coverage": round(len(grounded) / len(items), 4) if items else 0.0,
        "field_evidence_count": sum(len(values) for values in field_evidence.values()),
        "conflicts": conflicts,
        "inconsistencies": inconsistencies,
        "warnings": warnings,
    }


def _field_evidence_for_analysis(
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {field: [dict(evidence) for evidence in values] for field, values in field_evidence.items()}


def _snippet(evidence: Mapping[str, Any]) -> str | None:
    value = evidence.get("source_text")
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _analysis_evidence(
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    field_name: str,
) -> list[dict[str, Any]]:
    return [dict(item) for item in field_evidence.get(field_name, [])]


def _analysis_evidence_snippets(evidence: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        snippet
        for snippet in (_snippet(item) for item in evidence)
        if snippet
    ]


def _classify_visit_type(
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Classify visit only from an explicit label or a strong date pair."""
    visit_evidence = _analysis_evidence(field_evidence, "visit_type")
    admission_evidence = _analysis_evidence(field_evidence, "admission_date")
    discharge_evidence = _analysis_evidence(field_evidence, "discharge_date")
    explicit_visit = claim_data.get("visit_type")

    if explicit_visit not in (None, ""):
        value = canonical_visit_type(explicit_visit) or "unknown"
        return {
            "value": value,
            "status": "extracted" if value != "unknown" else "unknown",
            "confidence": 1.0 if value != "unknown" else None,
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
            "confidence": 0.9,
            "confidence_type": "heuristic_not_calibrated",
            "method": "admission_and_discharge_dates_present",
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
        }

    # An invoice, resume, or document type alone does not establish inpatient
    # versus outpatient. Keep the result unknown and show incomplete evidence.
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

    raw_values = [
        str(item.get("source_text") or "").strip()
        for item in evidence
        if str(item.get("source_text") or "").strip()
    ]
    unique_raw_values = list(dict.fromkeys(raw_values))
    if len(unique_raw_values) > 1:
        return {
            "value": "unknown",
            "status": "unknown",
            "confidence": None,
            "confidence_type": None,
            "method": "conflicting_explicit_claim_type_values",
            "raw_value": str(raw_value),
            "raw_values": unique_raw_values,
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
            "note": "Multiple claim-stage labels require analyst review.",
        }

    normalized_value = canonical_claim_case_type(str(raw_value))
    matched_value = normalized_value[0] if normalized_value else None
    matched_terms = list(normalized_value[1]) if normalized_value else []

    if matched_value is None:
        return {
            "value": "unknown",
            "status": "unknown",
            "confidence": None,
            "confidence_type": None,
            "method": "unrecognized_explicit_claim_type",
            "raw_value": str(raw_value),
            "evidence": _analysis_evidence_snippets(evidence),
            "evidence_refs": [item.get("extraction_id") for item in evidence],
            "note": "The explicit claim-stage label is not in the configured business mapping.",
        }

    return {
        "value": matched_value,
        "status": "derived",
        "confidence": 1.0,
        "confidence_type": "grounded_rule",
        "method": "explicit_claim_type_mapping",
        "raw_value": str(raw_value),
        "matched_terms": matched_terms,
        "evidence": _analysis_evidence_snippets(evidence),
        "evidence_refs": [item.get("extraction_id") for item in evidence],
    }


def derive_claim_analysis(
    claim_data: Mapping[str, Any],
    normalized_claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    validation: Mapping[str, Any],
    *,
    icd_suggestions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build analyst-only values with explicit status and deterministic evidence."""
    visit_analysis = _classify_visit_type(claim_data, field_evidence)
    claim_case_analysis = _classify_claim_case_type(claim_data, field_evidence)

    required_checks = (
        ("provider_name", "Provider name"),
        ("invoice_number", "Invoice number"),
        ("admission_date", "Admission date"),
        ("discharge_date", "Discharge date"),
        ("diagnosis", "Diagnosis"),
    )
    missing_fields: list[str] = []
    for field_name, label in required_checks:
        value = claim_data.get(field_name)
        if value is None or value == [] or value == "":
            missing_fields.append(field_name)
    missing_information = [label for field_name, label in required_checks if field_name in missing_fields]

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

    fraud_indicators: list[str] = []
    for inconsistency in inconsistencies:
        if "date" in inconsistency.lower() or "total" in inconsistency.lower() or "multiple explicit" in inconsistency.lower():
            fraud_indicators.append(inconsistency)
    investigation_recommended = bool(inconsistencies)

    diagnosis_values = list(claim_data.get("diagnosis", []))
    medication_values = list(claim_data.get("medications", []))
    procedure_values = list(claim_data.get("procedures", []))
    financial_parts: list[str] = []
    if claim_data.get("total_amount") is not None:
        financial_parts.append(f"total amount {claim_data['total_amount']}")
    if claim_data.get("currency") is not None:
        financial_parts.append(f"currency {claim_data['currency']}")
    if claim_data.get("invoice_number") is not None:
        financial_parts.append(f"invoice {claim_data['invoice_number']}")

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
        "Explicit financial evidence: " + (", ".join(financial_parts) if financial_parts else "none") + "."
    )
    claim_story = (
        f"The available Markdown contains {len(diagnosis_values)} diagnosis item(s) and "
        f"{len(medication_values)} grouped medication item(s). "
        f"Derived visit classification is {visit_analysis['value']} with status {visit_analysis['status']}."
    )

    return {
        "visit_type": visit_analysis,
        "claim_case_type": claim_case_analysis,
        "missing_information": missing_information,
        "missing_fields": missing_fields,
        "inconsistencies": inconsistencies,
        "potential_risks": potential_risks,
        "fraud_indicators": fraud_indicators,
        "investigation_recommended": investigation_recommended,
        "icd_suggestions": [dict(item) for item in (icd_suggestions or [])],
        "case_snapshot": case_snapshot,
        "medical_summary": medical_summary,
        "financial_summary": financial_summary,
        "claim_story": claim_story,
        "summary_status": "derived",
    }


def _field_evidence_map(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("grounding_status") != "grounded":
            continue
        result[str(item.get("field_name"))].append(_evidence(item))
    return dict(result)


def build_claim_contract(
    serialized: Mapping[str, Any],
    *,
    icd_suggestions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the business JSON from prepared LangExtract items.

    `claim_data` contains explicit values only.  Normalized values and all
    derived classifications are placed in separate top-level sections.
    """
    prepared = prepare_intermediate_items(serialized)
    items = list(prepared.get("items", []))
    grounded_items = [item for item in items if item.get("grounding_status") == "grounded"]
    field_evidence = _field_evidence_map(grounded_items)
    claim_data = _empty_claim_data()

    for field_name in SCALAR_FIELDS:
        values = [item for item in grounded_items if item.get("field_name") == field_name]
        if values:
            # Source span wins over model text; raw/model text remains in
            # provenance. This protects exact IDs, dates, names, and amounts.
            claim_data[field_name] = _value_from_item(values[0])

    for field_name, destination in list_destinations().items():
        claim_data.setdefault(destination, [])
        claim_data[destination].extend(
            _value_from_item(item)
            for item in grounded_items
            if item.get("field_name") == field_name
            and _value_from_item(item) != ""
        )
    if claim_data.get("doctor_names"):
        claim_data["doctor_name"] = claim_data["doctor_names"][0]
    else:
        claim_data["doctor_name"] = None

    grouped_records: dict[str, list[dict[str, Any]]] = {}
    for definition in group_definitions():
        grouped_records[definition.record_key] = _grouped_records(
            grounded_items,
            family=definition.family,
            fields=definition.fields,
            anchor_field=definition.anchor_field,
        )
    diagnosis_records = grouped_records["diagnosis_records"]
    medication_records = grouped_records["medication_records"]
    procedure_records = grouped_records["procedure_records"]
    invoice_records = grouped_records["invoice_records"]
    lab_results = grouped_records["lab_results"]
    for record in diagnosis_records:
        record["document_icd_code"] = record.get("icd_code")

    for record_key, records in grouped_records.items():
        claim_data[record_key] = records
    # Preserve historic consumer aliases: medication_records/lab_records hold
    # the same relationship records as medications/lab_results. The legacy
    # `medications` test key also remains the grouped medication relationship.
    claim_data["medication_records"] = medication_records
    claim_data["lab_records"] = lab_results
    claim_data["medications"] = medication_records
    claim_data["lab_results"] = lab_results
    claim_data["icd_code_records"] = [
        {
            "code": record.get("icd_code"),
            "group_id": record.get("group_id"),
            "source_type": "document",
        }
        for record in diagnosis_records
        if record.get("icd_code")
    ]
    claim_data["pre_existing_conditions"] = list(claim_data.get("pre_existing_conditions", []))

    normalized_claim_data, normalization_notes = _build_normalized_claim_data(claim_data, grounded_items)
    validation = _validate_claim(claim_data, normalized_claim_data, field_evidence, items)
    validation["normalization_notes"] = normalization_notes
    analyst_analysis = derive_claim_analysis(
        claim_data,
        normalized_claim_data,
        field_evidence,
        validation,
        icd_suggestions=icd_suggestions,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "claim_data": claim_data,
        "normalized_claim_data": normalized_claim_data,
        "provenance": {
            "source_type": "langextract_markdown",
            "source_characters": prepared.get("source_characters", 0),
            "intermediate_items": items,
            "field_evidence": _field_evidence_for_analysis(claim_data, field_evidence),
            "ungrounded_items": [
                item for item in items if item.get("grounding_status") != "grounded"
            ],
            "page_markers": prepared.get("page_markers", []),
        },
        "validation": validation,
        "analyst_analysis": analyst_analysis,
    }


def _example_extraction(
    field_name: str,
    text: str,
    *,
    group_id: str,
    group_type: str,
    role: str = "value",
) -> Any:
    _require_langextract()
    return lx.data.Extraction(
        extraction_class=LANGEXTRACT_CLASS,
        extraction_text=text,
        attributes={
            "field_name": field_name,
            "group_id": group_id,
            "group_type": group_type,
            "role": role,
            "fact_status": "explicit",
        },
    )


def build_examples() -> list[Any]:
    """Few-shot examples that teach explicit-only extraction and grouping."""
    _require_langextract()
    return [
        lx.data.ExampleData(
            text="Diagnosis: Acute appendicitis\nICD-10: K35.80",
            extractions=[
                _example_extraction(
                    "diagnosis",
                    "Acute appendicitis",
                    group_id="diagnosis_1",
                    group_type="diagnosis",
                ),
                _example_extraction(
                    "icd_code",
                    "K35.80",
                    group_id="diagnosis_1",
                    group_type="diagnosis",
                    role="attribute",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Omeprazole 20 mg twice daily for 5 days",
            extractions=[
                _example_extraction(
                    "medication",
                    "Omeprazole",
                    group_id="medication_1",
                    group_type="medication",
                ),
                _example_extraction(
                    "dosage",
                    "20 mg",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "frequency",
                    "twice daily",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "duration",
                    "5 days",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Invoice Number: INV-2026-0098\nInvoice Date: 20 September 2026\nTotal Amount: Rp 1.250.000",
            extractions=[
                _example_extraction(
                    "invoice_number",
                    "INV-2026-0098",
                    group_id="invoice_1",
                    group_type="invoice",
                ),
                _example_extraction(
                    "invoice_date",
                    "20 September 2026",
                    group_id="invoice_1",
                    group_type="invoice",
                    role="attribute",
                ),
                _example_extraction(
                    "total_amount",
                    "Rp 1.250.000",
                    group_id="invoice_1",
                    group_type="invoice",
                    role="attribute",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Medication: Amoxicillin 500 mg three times daily for 7 days\nMedication: Amoxicillin 500 mg twice daily for 5 days",
            extractions=[
                _example_extraction(
                    "medication",
                    "Amoxicillin",
                    group_id="medication_1",
                    group_type="medication",
                ),
                _example_extraction(
                    "dosage",
                    "500 mg",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "frequency",
                    "three times daily",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "duration",
                    "7 days",
                    group_id="medication_1",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "medication",
                    "Amoxicillin",
                    group_id="medication_2",
                    group_type="medication",
                ),
                _example_extraction(
                    "dosage",
                    "500 mg",
                    group_id="medication_2",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "frequency",
                    "twice daily",
                    group_id="medication_2",
                    group_type="medication",
                    role="attribute",
                ),
                _example_extraction(
                    "duration",
                    "5 days",
                    group_id="medication_2",
                    group_type="medication",
                    role="attribute",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Visit Type: Rawat Jalan\nClaim Type: SKD",
            extractions=[
                _example_extraction(
                    "visit_type",
                    "Rawat Jalan",
                    group_id="claim_1",
                    group_type="claim",
                ),
                _example_extraction(
                    "claim_type",
                    "SKD",
                    group_id="claim_1",
                    group_type="claim",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Claim Type: Kelengkapan",
            extractions=[
                _example_extraction(
                    "claim_type",
                    "Kelengkapan",
                    group_id="claim_1",
                    group_type="claim",
                ),
            ],
        ),
        lx.data.ExampleData(
            text="Claim Type: Tinjau ulang\nSupporting Document: SP4",
            extractions=[
                _example_extraction(
                    "claim_type",
                    "Tinjau ulang",
                    group_id="claim_1",
                    group_type="claim",
                ),
                _example_extraction(
                    "supporting_document",
                    "SP4",
                    group_id="document_1",
                    group_type="document",
                ),
            ],
        ),
    ]


FIELD_DESCRIPTIONS: dict[str, str] = field_descriptions()

# Only the names that are genuinely confusable go into the output schema. The
# prompt carries the full catalogue, so repeating all of them here would double
# the fixed cost of every request for no extra signal.
_SCHEMA_DESCRIPTION_FIELDS: tuple[str, ...] = (
    "account_no",
    "patient_id",
    "member_number",
    "claim_type",
    "visit_type",
    "hospitalization_date",
    "reason_of_visit",
    "amount_incurred",
    "icd_code",
)


def _field_guide() -> str:
    """Render the field_name enum as a readable catalogue for the prompt.

    Fields without a description are listed bare so the model still sees the
    complete allowlist, but the prompt budget is spent only on the ambiguous
    names that are easy to confuse.
    """
    lines: list[str] = []
    plain: list[str] = []
    for name in CLAIM_FIELDS:
        description = FIELD_DESCRIPTIONS.get(name)
        if description:
            lines.append(f"{name} : {description}")
        else:
            plain.append(name)
    if plain:
        lines.append("other_allowed_values : " + ", ".join(plain))
    return "\n".join(lines)


def build_extraction_schema() -> dict[str, Any]:
    """Return LangExtract's raw output-envelope JSON schema.

    The schema is intentionally not the final Claim JSON.  LangExtract's
    resolver expects an `extractions` array whose item key is the extraction
    class and whose attribute key is `<class>_attributes`.

    ``field_name`` carries a plain JSON-Schema ``description`` so the meaning of
    the ambiguous field names reaches the model through structured output as
    well as through the prompt.  Only standard keywords are used, which keeps
    the payload safe for OpenAI strict mode and compatible gateways.
    """
    _require_langextract()
    field_schema: dict[str, Any] = {"type": "string", "enum": list(CLAIM_FIELDS)}
    catalogue: list[str] = []
    # The prompt already carries the full field catalogue, so the schema
    # description only repeats the handful of names that are most often
    # confused.  Keeping both payloads in sync is not worth the token cost.
    for name in _SCHEMA_DESCRIPTION_FIELDS:
        description = FIELD_DESCRIPTIONS.get(name)
        if description:
            catalogue.append(f"{name} = {description}")
    if catalogue:
        field_schema["description"] = "Pick exactly one field name. " + "; ".join(catalogue)
    item_schema = lx.schema.extraction_item_schema(
        LANGEXTRACT_CLASS,
        attributes={
            "field_name": field_schema,
            "group_id": {"type": "string"},
            "group_type": {"type": "string", "enum": list(GROUP_TYPES)},
            "role": {
                "type": "string",
                "enum": ["value", "identifier", "attribute", "context"],
            },
            "fact_status": {"type": "string", "enum": ["explicit"]},
        },
    )
    return lx.schema.extractions_schema(item_schema)


def build_prompt() -> str:
    field_guide = _field_guide()
    return textwrap.dedent(
        f"""
        You are the factual extraction stage for an insurance claim analyst.
        The input is Markdown produced by a document OCR/VLM stage. Extract only
        values that are explicitly present in the input text.

        Hard rules:
        1. Emit one {LANGEXTRACT_CLASS} item for every relevant occurrence.
        2. extraction_text MUST be a verbatim, minimal source span. Never
           paraphrase, expand abbreviations, or include a field label unless
           the label itself is the value.
        3. Use field_name to select exactly one value. Field meanings:
{field_guide}
        4. fact_status is always "explicit". Do not emit unknown, inferred,
           suggested, risk, summary, confidence, or classification values.
        5. Do not infer an ICD code from a diagnosis. Emit icd_code only when
           an ICD code is visibly written in the document.
        6. Do not infer visit type. Emit visit_type only when the document
           explicitly states inpatient, outpatient, rawat inap, or rawat jalan.
        7. Emit claim_type only when a claim stage/type is explicitly written
           (for example SKD, Formulir, Kelengkapan, or Tinjau ulang). SP4 is
           a supporting document unless the document explicitly labels it as
           the claim stage; never infer a stage from the filename alone.
        8. Use the same group_id for related items: diagnosis and its explicit
           ICD code; medication and dosage/frequency/duration; invoice number,
           invoice date, and amounts; a lab result and its attributes.
        9. Use a different group_id for a different occurrence, even when the
           text is identical at another page or character range.
        10. Do not deduplicate repeated occurrences. The Python merger will
            deduplicate only identical character spans.
        11. Keep exact spelling, punctuation, leading zeros, amounts, dates,
            names, identifiers, and codes. Do not normalize values here.
        12. If a field is absent, emit nothing for that field. Never fabricate
            a placeholder extraction.
        13. Extract supporting_document only when a document/supporting
            document is explicitly named in the source.
        """
    ).strip()


def _model_config(config: AppConfig) -> Any:
    _require_langextract()
    from langextract.factory import ModelConfig

    provider = config.langextract_provider.lower()
    model_id = config.langextract_model
    provider_kwargs: dict[str, Any] = {}
    api_key = (
        config.langextract_api_key
        or os.getenv("LANGEXTRACT_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    if provider in {"openai", "gemini"} and api_key:
        provider_kwargs["api_key"] = api_key
    if provider == "openai" and config.langextract_base_url:
        provider_kwargs["base_url"] = config.langextract_base_url
    if provider == "ollama":
        provider_kwargs["base_url"] = (
            config.langextract_base_url or "http://localhost:11434"
        )
    return ModelConfig(
        model_id=model_id,
        provider=provider,
        provider_kwargs=provider_kwargs,
    )


def _supports_output_schema(provider: str) -> bool:
    return provider.lower() in {"openai", "gemini"}


def _make_usage_recording_model(config: AppConfig) -> tuple[Any, UsageLedger]:
    """Build a LangExtract provider whose SDK client records token usage.

    LangExtract throws the provider ``usage`` payload away: its OpenAI adapter
    returns ``ScoredOutput(score, output)`` and never surfaces
    ``response.usage``.  Rather than patch the adapter, this wraps the client
    object the adapter already created and tallies the usage block from every
    response.  The wrapping is defensive on purpose: if a future LangExtract
    stops exposing ``_client``, or uses an unexpected client shape, the model
    still works and the stage simply falls back to tiktoken estimates.

    Returns the model plus the ledger that collected its usage.
    """
    _require_langextract()
    from langextract import factory

    model = factory.create_model(
        config=_model_config(config),
        use_schema_constraints=False,
    )
    ledger = UsageLedger()
    return _attach_usage_recorder(model, ledger), ledger


def _attach_usage_recorder(model: Any, ledger: UsageLedger) -> Any:
    """Replace ``model._client`` with a proxy that records usage responses."""
    client = getattr(model, "_client", None)
    if client is None:
        return model

    chat = getattr(client, "chat", None)
    completions = getattr(chat, "completions", None) if chat is not None else None
    if completions is not None and hasattr(completions, "create"):
        model._client = _OpenAIUsageRecordingClient(
            client,
            ledger,
            _openai_create_wrapper(completions.create, ledger),
        )
        return model

    models = getattr(client, "models", None)
    generate = getattr(models, "generate_content", None) if models is not None else None
    if generate is not None and callable(generate):
        model._client = _GeminiUsageRecordingClient(
            client,
            ledger,
            _gemini_generate_wrapper(generate, ledger),
        )
    return model


class _RecordingProxy:
    """Attribute-forwarding proxy that delegates everything to the real SDK."""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_inner"), name)


class _OpenAIUsageRecordingClient(_RecordingProxy):
    def __init__(self, inner: Any, ledger: UsageLedger, create: Any) -> None:
        super().__init__(inner)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(
            self,
            "chat",
            _RecordingNamespace(
                inner.chat,
                completions=_RecordingNamespace(
                    inner.chat.completions,
                    create=create,
                ),
            ),
        )


class _GeminiUsageRecordingClient(_RecordingProxy):
    def __init__(self, inner: Any, ledger: UsageLedger, generate: Any) -> None:
        super().__init__(inner)
        object.__setattr__(self, "_ledger", ledger)
        object.__setattr__(
            self,
            "models",
            _RecordingNamespace(inner.models, generate_content=generate),
        )


class _RecordingNamespace(_RecordingProxy):
    """Proxy that overrides selected attributes and forwards the rest."""

    def __init__(self, inner: Any, **overrides: Any) -> None:
        super().__init__(inner)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, "_overrides")
        if name in overrides:
            return overrides[name]
        return getattr(object.__getattribute__(self, "_inner"), name)


def _openai_create_wrapper(create: Any, ledger: UsageLedger) -> Any:
    def create_with_usage(*args: Any, **kwargs: Any) -> Any:
        response = create(*args, **kwargs)
        ledger.record(getattr(response, "usage", None))
        return response

    return create_with_usage


def _gemini_generate_wrapper(generate: Any, ledger: UsageLedger) -> Any:
    def generate_with_usage(*args: Any, **kwargs: Any) -> Any:
        response = generate(*args, **kwargs)
        ledger.record(getattr(response, "usage_metadata", None))
        return response

    return generate_with_usage


def _save_annotated_jsonl(
    result: Any,
    *,
    output_dir: str | Path | None,
    output_name: str,
) -> str | None:
    if output_dir is None or lx is None:
        return None
    try:
        lx.io.save_annotated_documents(
            [result] if not isinstance(result, (list, tuple)) else list(result),
            output_dir=output_dir,
            output_name=output_name,
            show_progress=False,
        )
        return str(Path(output_dir) / output_name)
    except Exception:
        # The normalized intermediate JSONL is still written by the caller.
        return None


def _save_intermediate_jsonl(
    items: Sequence[Mapping[str, Any]],
    *,
    output_dir: str | Path | None,
    output_name: str,
) -> str | None:
    if output_dir is None:
        return None
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / output_name
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(dict(item), ensure_ascii=False, default=str) + "\n")
    return str(path)


def _build_icd_suggestion_schema() -> dict[str, Any]:
    _require_langextract()
    item_schema = lx.schema.extraction_item_schema(
        ICD_SUGGESTION_CLASS,
        attributes={
            "diagnosis_group": {"type": "string"},
            "diagnosis": {"type": "string"},
            "suggested_icd_code": {"type": "string"},
            "confidence": {"type": "string"},
            "status": {"type": "string", "enum": ["derived"]},
        },
    )
    return lx.schema.extractions_schema(item_schema)


def _build_icd_suggestion_examples() -> list[Any]:
    _require_langextract()
    return [
        lx.data.ExampleData(
            text="Diagnosis: Acute appendicitis",
            extractions=[
                lx.data.Extraction(
                    extraction_class=ICD_SUGGESTION_CLASS,
                    extraction_text="Acute appendicitis",
                    attributes={
                        "diagnosis_group": "diagnosis_1",
                        "diagnosis": "Acute appendicitis",
                        "suggested_icd_code": "K35.80",
                        "confidence": "0.82",
                        "status": "derived",
                    },
                )
            ],
        )
    ]


def _run_icd_suggestion_pass(
    *,
    claim_data: Mapping[str, Any],
    field_evidence: Mapping[str, Sequence[Mapping[str, Any]]],
    config: AppConfig,
    return_usage: bool = False,
) -> Any:
    """Optional, isolated application-level ICD suggestion pass.

    LangExtract does not contain an ICD coding ontology.  When explicitly
    enabled, this function makes a second model request using only grounded
    diagnosis text and stores the result under analyst_analysis only.

    With ``return_usage`` the second element of the tuple is the usage report
    for that isolated pass; otherwise only the suggestions are returned.
    """
    if not config.langextract_enable_icd_suggestions:
        return ([], None) if return_usage else []
    diagnoses = list(claim_data.get("diagnosis", []))
    if not diagnoses:
        return ([], None) if return_usage else []
    _require_langextract()
    diagnosis_evidence = list(field_evidence.get("diagnosis", []))
    evidence_text = "\n".join(
        str(item.get("source_text") or "") for item in diagnosis_evidence
    ).strip()
    if not evidence_text:
        return ([], None) if return_usage else []

    prompt = textwrap.dedent(
        """
        Suggest an ICD-10 code only for the diagnosis text below. This is
        analyst assistance, not a document fact. Return JSON with an array
        named suggestions. Each item must contain diagnosis_group, diagnosis,
        suggested_icd_code, confidence, and status. Use status="derived".
        Do not return a code if the diagnosis is too ambiguous. Confidence is
        the model's self-reported value, not a calibrated probability.
        """
    ).strip()
    ledger = UsageLedger()
    try:
        model, ledger = _make_usage_recording_model(config)
        suggestion_kwargs: dict[str, Any] = {
            "text_or_documents": evidence_text,
            "prompt_description": prompt,
            "examples": _build_icd_suggestion_examples(),
            "model": model,
            "use_schema_constraints": False,
            "max_char_buffer": min(2000, max(256, config.langextract_max_char_buffer)),
            "max_workers": 1,
            "batch_length": 1,
            "extraction_passes": 1,
            "temperature": 0.0,
            "show_progress": True,
            "resolver_params": {"suppress_parse_errors": True},
        }
        if config.langextract_use_output_schema and _supports_output_schema(
            config.langextract_provider
        ):
            suggestion_kwargs["output_schema"] = _build_icd_suggestion_schema()
        result = lx.extract(**suggestion_kwargs)
        suggestions = _extract_json_suggestions(result)
    except Exception:
        return ([], None) if return_usage else []

    output: list[dict[str, Any]] = []
    for suggestion in suggestions:
        code = str(suggestion.get("suggested_icd_code") or "").strip()
        diagnosis = str(suggestion.get("diagnosis") or "").strip()
        if not code or not diagnosis:
            continue
        if not re.fullmatch(r"[A-Za-z][0-9A-Za-z.]{1,15}", code):
            continue
        confidence_value = suggestion.get("confidence")
        try:
            confidence = float(confidence_value)
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))
        output.append(
            {
                "diagnosis": diagnosis,
                "suggested_icd_code": code,
                "status": "derived",
                "confidence": confidence,
                "confidence_type": "model_self_reported_not_calibrated",
                "evidence": [evidence_text],
                "evidence_refs": [item.get("extraction_id") for item in diagnosis_evidence],
                "source_type": "separate_icd_analysis_pass",
            }
        )

    if not return_usage:
        return output

    if not ledger.has_provider_usage and config.usage_estimate_fallback:
        ledger.estimate(
            input_text=prompt + evidence_text,
            output_text=json.dumps(output, ensure_ascii=False, default=str),
            requests=ledger.requests or 1,
        )
    input_rate, output_rate = config.rates_for("langextract")
    return output, build_usage_report(
        ledger,
        stage="icd_suggestion",
        model=config.langextract_icd_suggestion_model or config.langextract_model,
        provider=config.langextract_provider,
        usd_to_idr=config.usd_to_idr,
        input_rate_per_million=input_rate,
        output_rate_per_million=output_rate,
    )


def _extract_json_suggestions(result: Any) -> list[dict[str, Any]]:
    """Best-effort parser for the optional, non-authoritative ICD pass."""
    # LangExtract returns AnnotatedDocument objects, not a final business JSON
    # object. The optional pass is intentionally conservative: if a provider
    # returns ordinary structured extractions, no suggestion is promoted.
    suggestions: list[dict[str, Any]] = []
    for document in _annotated_documents(result):
        for extraction in getattr(document, "extractions", None) or []:
            attributes = _attribute_dict(extraction)
            if attributes.get("suggested_icd_code"):
                suggestions.append(
                    {
                        "diagnosis_group": attributes.get("diagnosis_group", ""),
                        "diagnosis": attributes.get("diagnosis", ""),
                        "suggested_icd_code": attributes.get("suggested_icd_code", ""),
                        "confidence": attributes.get("confidence"),
                        "status": attributes.get("status", "derived"),
                    }
                )
    return suggestions


def extract_claim_from_markdown(
    markdown: str,
    *,
    config: AppConfig,
    output_dir: str | Path | None = None,
    output_stem: str = "claim",
    icd_suggestions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run the LangExtract stage and return an auditable claim contract.

    The caller supplies Markdown only. No page image or keyword-filtered text
    is sent to this function.
    """
    _require_langextract()
    config.validate_for_langextract()
    if not markdown or not markdown.strip():
        raise ValueError("Markdown kosong; LangExtract tidak dapat menjalankan extraction.")

    prompt = build_prompt()
    schema = None
    if config.langextract_use_output_schema:
        if not _supports_output_schema(config.langextract_provider):
            raise LangExtractConfigurationError(
                "output_schema hanya didukung oleh provider OpenAI/Gemini; "
                "set LANGEXTRACT_USE_OUTPUT_SCHEMA=false untuk Ollama."
            )
        schema = build_extraction_schema()

    max_workers = max(1, int(config.langextract_max_workers))
    batch_length = max(max_workers, int(config.langextract_batch_length))
    icd_usage: dict[str, Any] | None = None
    # A pre-built model is passed instead of ``config=`` so the client can be
    # wrapped with a usage recorder; LangExtract gives ``model=`` precedence and
    # still applies ``output_schema`` exactly as before.
    model, usage_ledger = _make_usage_recording_model(config)
    extract_kwargs: dict[str, Any] = {
        "text_or_documents": markdown,
        "prompt_description": prompt,
        "examples": build_examples(),
        "model": model,
        "use_schema_constraints": False,
        "max_char_buffer": int(config.langextract_max_char_buffer),
        "max_workers": max_workers,
        "batch_length": batch_length,
        "extraction_passes": int(config.langextract_extraction_passes),
        "context_window_chars": config.langextract_context_window_chars,
        "temperature": 0.0,
        "show_progress": bool(config.langextract_show_progress),
        "resolver_params": {"suppress_parse_errors": True},
    }
    if schema is not None:
        extract_kwargs["output_schema"] = schema

    result = lx.extract(**extract_kwargs)
    serialized = serialize_langextract_result(result, markdown=markdown)
    prepared = prepare_intermediate_items(serialized)
    contract = build_claim_contract(
        prepared,
        icd_suggestions=icd_suggestions,
    )
    # Recommend an ICD code only when a diagnosis was found but the document
    # carries none. Running the pass unconditionally would add a model call
    # per document for cases that need no suggestion at all.
    needs_icd_recommendation = bool(contract["claim_data"].get("diagnosis")) and not (
        contract["claim_data"].get("icd_codes")
    )
    should_suggest = config.langextract_enable_icd_suggestions or needs_icd_recommendation
    if not icd_suggestions and should_suggest:
        suggestions, icd_usage = _run_icd_suggestion_pass(
            claim_data=contract["claim_data"],
            field_evidence=contract["provenance"]["field_evidence"],
            config=config,
            return_usage=True,
        )
        if suggestions:
            contract["analyst_analysis"] = derive_claim_analysis(
                contract["claim_data"],
                contract["normalized_claim_data"],
                contract["provenance"]["field_evidence"],
                contract["validation"],
                icd_suggestions=suggestions,
            )

    annotated_path = _save_annotated_jsonl(
        result,
        output_dir=output_dir,
        output_name=f"{output_stem}.langextract.jsonl",
    )
    intermediate_path = _save_intermediate_jsonl(
        contract["provenance"]["intermediate_items"],
        output_dir=output_dir,
        output_name=f"{output_stem}.extraction_items.jsonl",
    )

    effective_parameters = {
        "provider": config.langextract_provider,
        "model": config.langextract_model,
        "max_char_buffer": int(config.langextract_max_char_buffer),
        "max_workers": max_workers,
        "batch_length": batch_length,
        "extraction_passes": int(config.langextract_extraction_passes),
        "context_window_chars": config.langextract_context_window_chars,
        "output_schema_enabled": schema is not None,
        "show_progress": bool(config.langextract_show_progress),
    }
    input_rate, output_rate = config.rates_for("langextract")
    if not usage_ledger.has_provider_usage and config.usage_estimate_fallback:
        # The provider stayed silent. Estimate from what we know was sent and
        # returned; LangExtract chunks the Markdown internally, so the chunking
        # overhead is not modelled and the note says so.
        usage_ledger.estimate(
            input_text=prompt,
            output_text=json.dumps(serialized, ensure_ascii=False, default=str),
            requests=usage_ledger.requests or 1,
        )
        estimate_note = (
            "Provider tidak mengirim blok usage. Estimasi memakai prompt, "
            "few-shot examples, dan Markdown input; overhead chunking internal "
            "LangExtract tidak dimodelkan."
        )
    else:
        estimate_note = None

    usage = build_usage_report(
        usage_ledger,
        stage="langextract",
        model=config.langextract_model,
        provider=config.langextract_provider,
        usd_to_idr=config.usd_to_idr,
        input_rate_per_million=input_rate,
        output_rate_per_million=output_rate,
        note=estimate_note,
    )
    if icd_usage:
        usage["icd_suggestion"] = icd_usage

    return {
        "model": config.langextract_model,
        "data": contract,
        "intermediate": contract["provenance"]["intermediate_items"],
        "debug_prompts": {
            "system": "LangExtract factual extraction stage; source is Markdown only.",
            "user_text": prompt,
            "output_schema": schema,
            "parameters": effective_parameters,
            "image_pages": [],
            "source_characters": len(markdown),
        },
        "usage": usage,
        "cost": usage["cost"],
        "raw_response": {},
        "artifacts": {
            "langextract_jsonl_path": annotated_path,
            "intermediate_jsonl_path": intermediate_path,
        },
        "extraction_engine": "langextract",
        "output_schema": schema,
    }


__all__ = [
    "CLAIM_FIELDS",
    "LangExtractConfigurationError",
    "LangExtractUnavailableError",
    "build_claim_contract",
    "build_examples",
    "build_extraction_schema",
    "build_prompt",
    "derive_claim_analysis",
    "extract_claim_from_markdown",
    "prepare_intermediate_items",
    "serialize_langextract_result",
]