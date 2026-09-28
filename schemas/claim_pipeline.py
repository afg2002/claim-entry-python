from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VisitTypeAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: str = "unknown"
    status: str = "unknown"
    confidence: float | None = None
    evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class ClaimCaseTypeAnalysis(VisitTypeAnalysis):
    """Canonical claim stage derived from an explicit claim_type label."""


class AnalystAnalysis(BaseModel):
    model_config = ConfigDict(extra="allow")

    visit_type: VisitTypeAnalysis
    claim_case_type: ClaimCaseTypeAnalysis
    missing_information: list[str] = Field(default_factory=list)
    inconsistencies: list[str] = Field(default_factory=list)
    potential_risks: list[str] = Field(default_factory=list)
    fraud_indicators: list[str] = Field(default_factory=list)
    investigation_recommended: bool = False
    icd_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    case_snapshot: str | None = None
    medical_summary: str | None = None
    financial_summary: str | None = None
    claim_story: str | None = None


class ClaimContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    claim_data: dict[str, Any]
    normalized_claim_data: dict[str, Any]
    provenance: dict[str, Any]
    validation: dict[str, Any]
    analyst_analysis: AnalystAnalysis


CLAIM_CONTRACT_JSON_SCHEMA = ClaimContract.model_json_schema()


def validate_claim_contract(contract: dict[str, Any]) -> ClaimContract:
    return ClaimContract.model_validate(contract)


__all__ = [
    "AnalystAnalysis",
    "CLAIM_CONTRACT_JSON_SCHEMA",
    "ClaimCaseTypeAnalysis",
    "ClaimContract",
    "VisitTypeAnalysis",
    "validate_claim_contract",
]