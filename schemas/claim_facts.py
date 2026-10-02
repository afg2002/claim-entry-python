"""Canonical ClaimFacts contract (flow.md Layer 2) + the camelCase rename table.

Per ``flow.md``:

* Layer 2 ``ClaimFacts`` is the **factual** layer every reasoning step reads.
* Layer 2 examples are written in **camelCase** (``insuredName``, ``providerNames``,
  ``amountIncurred``), so this module is the single place where the
  snake_case contract is translated to the camelCase contract.
* Principle 1 (Claim Entry UI is the contract) governs the **output** payload.
  The rename here must therefore never invent a *different meaning*; it changes
  spelling, and only where ``flow.md`` explicitly names something differently.

Design rules
------------

1. **The rename is mechanical by default.** ``to_camel()`` converts
   ``snake_case`` to ``camelCase``. Only names where ``flow.md`` Layer 2 states a
   different word get an entry in :data:`CAMEL_OVERRIDES`.
2. **Semantic changes are NOT part of the rename.** ``flow.md`` also shows
   ``providerNames`` / ``diagnoses`` (plural) where the contract today is a
   scalar plus a separate ``*_list`` destination. Plurality changes the
   extraction shape, so those are listed in :data:`DEFERRED_SEMANTIC_CHANGES`
   and need their own phase. See the module note on :data:`CLAIM_FACT_ALIASES`.
3. **Non-fact keys must never be renamed.** Containers, provenance metadata and
   analyst sections are Python/JSON plumbing, not claim facts; they are listed in
   :data:`FACT_CONTAINER_KEYS` so a rename pass cannot touch them by accident.
4. **The table is validated against the field registry.** ``validate_aliases()``
   proves the alias table and ``services.claim_fields.FIELD_SPECS`` agree, so
   adding a field cannot silently skip the rename.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Name translation
# ---------------------------------------------------------------------------

_CAMEL_BOUNDARY = re.compile(r"_([a-z0-9])")


def to_camel(name: str) -> str:
    """Return the camelCase spelling of a snake_case claim-fact name."""
    if not name:
        return name
    head, *tail = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


# Names where flow.md Layer 2 states a different WORD, not just a different case.
# Key = current snake_case contract name, value = flow.md camelCase name.
CAMEL_OVERRIDES: dict[str, str] = {
    "patient_name": "insuredName",  # flow.md Layer 2 / Principle 1 use insuredName.
}


def camel_name(name: str) -> str:
    """Return the flow.md camelCase name for one snake_case claim-fact name."""
    return CAMEL_OVERRIDES.get(name, to_camel(name))


# ---------------------------------------------------------------------------
# Keys that are plumbing, not claim facts -- never renamed
# ---------------------------------------------------------------------------

FACT_CONTAINER_KEYS: frozenset[str] = frozenset(
    {
        # contract sections
        "schema_version",
        "claim_data",
        "normalized_claim_data",
        "provenance",
        "validation",
        "analyst_analysis",
        # provenance / item metadata
        "source_type",
        "source_characters",
        "intermediate_items",
        "field_evidence",
        "ungrounded_items",
        "page_markers",
        "field_name",
        "extraction_text",
        "source_value",
        "source_text",
        "char_start",
        "char_end",
        "source_page",
        "alignment_status",
        "grounding_status",
        "exact_match",
        "extraction_id",
        "group_id",
        "model_group_id",
        "group_type",
        "fact_status",
        "role",
        # validation metadata
        "grounded_item_count",
        "ungrounded_item_count",
        "source_coverage",
        "field_evidence_count",
        "conflicts",
        "inconsistencies",
        "warnings",
        "normalization_notes",
        # analyst-analysis section keys
        "visit_type",
        "claim_case_type",
        "missing_information",
        "missing_fields",
        "potential_risks",
        "fraud_indicators",
        "investigation_recommended",
        "icd_suggestions",
        "case_snapshot",
        "medical_summary",
        "financial_summary",
        "claim_story",
        "summary_status",
        "decisions",
        "claim_signals",
        "name_check",
        # usage stage keys
        "vlm_ocr",
        "legacy_page_vlm",
        "icd_suggestion",
        "document_total",
        "reported",
        "estimated",
        "total",
        "cost",
        "available",
        "stages",
        "requests",
    }
)