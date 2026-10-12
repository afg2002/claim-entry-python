"""Pre-VLM gate for Docling.

A cheap RapidOCR pass runs before Remote VLM. Regex and RapidFuzz inspect the
OCR text for known claim-form labels. If enough fields are confidently mapped,
the remote VLM OCR stage is skipped and the existing local OCR + LangExtract
path is used. Otherwise the original Remote VLM path is preserved unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import AppConfig
from services.pre_vlm_mapping import DEFAULT_ALIASES, map_fields, summarize_cost_saving
from .docling_parser import parse_docling as _parse_docling


PRE_VLM_FIELDS = tuple(DEFAULT_ALIASES.keys())


def _run_preflight(
    source: Path,
    output_dir: Path,
    config: AppConfig,
    *,
    analyze_all_pages: bool,
) -> dict[str, Any]:
    """Run only local OCR and score whether remote VLM is necessary."""
    if not config.pre_vlm_mapping_enabled:
        return {
            "enabled": False,
            "should_use_vlm": True,
            "reason": "pre_vlm_mapping_disabled",
            "fields": {},
        }

    # OCR must stay local here. This call cannot hit the remote model.
    result = _parse_docling(
        source,
        output_dir / "pre_vlm",
        information_extraction=False,
        analyze_all_pages=analyze_all_pages,
        extraction_profile="Auto-detect",
        llm_config=config,
        ocr_engine="RapidOCR",
    )
    if result.get("status") != "success":
        return {
            "enabled": True,
            "should_use_vlm": True,
            "reason": "preflight_ocr_failed",
            "error": result.get("error"),
            "fields": {},
        }

    markdown = str(result.get("markdown") or "")
    fields = map_fields(
        markdown,
        PRE_VLM_FIELDS,
        fuzzy_threshold=config.pre_vlm_fuzzy_threshold if config.pre_vlm_fuzzy_enabled else 101.0,
        fuzzy_margin=config.pre_vlm_fuzzy_margin,
    )
    confident = [item for item in fields.values() if not item["needs_vlm"]]
    coverage = len(confident) / len(fields) if fields else 0.0
    should_use_vlm = not (
        len(confident) >= config.pre_vlm_min_matches
        and coverage >= config.pre_vlm_min_coverage
    )

    return {
        "enabled": True,
        "should_use_vlm": should_use_vlm,
        "reason": "mapping_confident" if not should_use_vlm else "mapping_insufficient",
        "ocr_engine": "RapidOCR",
        "fields": fields,
        "matched_fields": len(confident),
        "total_fields": len(fields),
        "coverage": round(coverage, 4),
        "markdown_chars": len(markdown),
    }


def parse_docling(
    file_path: str | Path,
    output_dir: str | Path,
    *,
    information_extraction: bool = True,
    analyze_all_pages: bool = True,
    extraction_profile: str = "Auto-detect",
    llm_config: AppConfig | None = None,
    ocr_engine: str = "Remote VLM",
) -> dict[str, Any]:
    """Public parser entrypoint with an optional pre-VLM cost gate."""
    config = llm_config or AppConfig.from_file()
    source = Path(file_path).resolve()
    out = Path(output_dir).resolve()

    if ocr_engine != "Remote VLM" or not config.pre_vlm_mapping_enabled:
        return _parse_docling(
            source,
            out,
            information_extraction=information_extraction,
            analyze_all_pages=analyze_all_pages,
            extraction_profile=extraction_profile,
            llm_config=config,
            ocr_engine=ocr_engine,
        )

    preflight = _run_preflight(
        source,
        out,
        config,
        analyze_all_pages=analyze_all_pages,
    )

    selected_engine = "Remote VLM" if preflight["should_use_vlm"] else "RapidOCR"
    result = _parse_docling(
        source,
        out,
        information_extraction=information_extraction,
        analyze_all_pages=analyze_all_pages,
        extraction_profile=extraction_profile,
        llm_config=config,
        ocr_engine=selected_engine,
    )

    total_pages = len((result.get("json_data") or {}).get("raw_ocr_pages", [])) or 1
    skipped_pages = total_pages if selected_engine == "RapidOCR" else 0
    savings = summarize_cost_saving(
        total_pages,
        skipped_pages,
        estimated_vlm_cost_per_page_idr=config.pre_vlm_cost_per_page_idr,
    )

    json_data = result.setdefault("json_data", {})
    json_data["pre_vlm_mapping"] = {
        **preflight,
        "selected_engine": selected_engine,
        "cost_optimization": savings,
    }
    result["pre_vlm_mapping"] = json_data["pre_vlm_mapping"]
    result["json_data"] = json_data

    # Keep the audit trail in the generated JSON/markdown metadata without
    # changing the existing claim contract.
    artifacts = result.get("artifacts") or {}
    json_path = artifacts.get("json_path")
    if json_path:
        import json
        path = Path(json_path)
        if path.exists():
            path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return result
