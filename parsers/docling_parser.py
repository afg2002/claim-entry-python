from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import fitz

from config import AppConfig
from schemas import NATIVE_SCHEMA_REGISTRY
from services import extract_claim_from_markdown, extract_with_remote_vlm
from services.claim_form import (
    apply_document_claim_type,
    build_claim_form,
    build_full_claim_json,
)
from services.page_classifier import classify_pages
from services.usage_tracker import (
    combine_usage_reports,
    unavailable_usage_report,
    vlm_usage_from_conversion,
)


def _vlm_usage(
    converted: Any,
    config: AppConfig,
    ocr_engine: str,
    markdown: str,
) -> dict[str, Any]:
    """Collect Remote VLM token usage that Docling recorded per page.

    Local OCR engines never call a remote model, so they get an explicit
    "not applicable" report rather than a row of zeros that looks like free
    inference.
    """
    if ocr_engine != "Remote VLM":
        return unavailable_usage_report(
            stage="vlm_ocr",
            note=f"Engine OCR `{ocr_engine}` berjalan lokal tanpa panggilan model.",
            model=getattr(config, "vlm_model", config.model),
        )

    input_rate, output_rate = config.rates_for("vlm")
    return vlm_usage_from_conversion(
        converted,
        model=getattr(config, "vlm_model", config.model),
        usd_to_idr=config.usd_to_idr,
        input_rate_per_million=input_rate,
        output_rate_per_million=output_rate,
        estimated_input_text=markdown if config.usage_estimate_fallback else "",
        estimated_output_text=markdown if config.usage_estimate_fallback else "",
    )


def _resolve_extraction_profile(extraction_profile: str | None, markdown: str | None = None) -> str:
    """Project policy: insurance claim documents must always use ClaimAnalysis."""
    requested = (extraction_profile or "").strip()
    if requested in {"Insurance claim", "Claim analysis", "InsuranceClaim", "ClaimAnalysis", "Insurance Claim", "claim_analysis"}:
        return "Insurance claim"
    if requested and requested != "Auto-detect":
        return requested
    return "Insurance claim"


def _detect_document_profile(markdown: str) -> dict[str, Any]:
    text = markdown.lower()
    signals = {
        "Insurance claim": {
            "claim": 4,
            "klaim": 4,
            "policy": 3,
            "polis": 3,
            "reimbursement": 3,
            "tertanggung": 3,
            "insurance": 2,
            "asuransi": 2,
        },
        "Invoice": {
            "invoice": 4,
            "faktur": 4,
            "subtotal": 3,
            "due date": 2,
            "total amount": 2,
            "qty": 2,
            "quantity": 2,
        },
        "Medical document": {
            "prescription": 4,
            "resep": 4,
            "diagnosis": 3,
            "diagnosa": 3,
            "medication": 3,
            "obat": 3,
            "dosage": 3,
            "dosis": 3,
            "patient": 2,
            "pasien": 2,
        },
    }
    scores = {
        profile: sum(weight for phrase, weight in terms.items() if phrase in text)
        for profile, terms in signals.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    selected_profile, selected_score = ranked[0]
    total_score = sum(scores.values())
    confidence = round(selected_score / total_score, 2) if total_score else 0.0

    if selected_score < 3 or confidence < 0.45:
        selected_profile = "General document"

    matched_signals = [
        phrase
        for phrase, weight in signals.get(selected_profile, {}).items()
        if phrase in text
    ]
    return {
        "profile": selected_profile,
        "confidence": confidence,
        "scores": scores,
        "matched_signals": matched_signals,
        "method": "keyword signals from Docling Markdown",
    }


def _render_pages(file_path: Path, include_all_pages: bool) -> list[tuple[int, bytes, str]]:
    document = fitz.open(file_path)
    try:
        page_indexes = range(document.page_count)
        if not include_all_pages:
            page_indexes = range(min(document.page_count, 1))
        rendered: list[tuple[int, bytes, str]] = []
        for page_index in page_indexes:
            pixmap = document[page_index].get_pixmap(
                matrix=fitz.Matrix(1.5, 1.5),
                alpha=False,
            )
            rendered.append((page_index + 1, pixmap.tobytes("png"), "image/png"))
        return rendered
    finally:
        document.close()


def _raw_ocr_pages_from_docling(raw_data: dict[str, Any], markdown: str) -> list[dict[str, Any]]:
    page_texts: dict[int, list[str]] = {}
    for item in raw_data.get("texts", []):
        text = item.get("text") or item.get("orig")
        page_numbers = {
            int(provenance.get("page_no"))
            for provenance in item.get("prov", [])
            if provenance.get("page_no") is not None
        }
        for page_number in page_numbers:
            if text:
                page_texts.setdefault(page_number, []).append(str(text))

    if not page_texts:
        return [{"page_number": "document", "raw_text": markdown, "errors": []}]

    return [
        {
            "page_number": page_number,
            "raw_text": "\n\n".join(texts),
            "errors": [],
        }
        for page_number, texts in sorted(page_texts.items())
    ]


def _convert_document(source: Path, config: AppConfig, ocr_engine: str):
    """Run Docling conversion with the selected local or remote OCR engine."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        RapidOcrOptions,
        TesseractOcrOptions,
        VlmConvertOptions,
        VlmPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    if ocr_engine == "Remote VLM":
        from docling.datamodel.vlm_engine_options import (
            ApiVlmEngineOptions,
            VlmEngineType,
        )
        from docling.pipeline.vlm_pipeline import VlmPipeline

        api_url = config.base_url.rstrip("/")
        if not api_url.endswith("/chat/completions"):
            api_url += "/chat/completions"

        vlm_options = VlmConvertOptions.from_preset(
            "granite_vision",
            engine_options=ApiVlmEngineOptions(
                engine_type=VlmEngineType.API,
                url=api_url,
                headers={"Authorization": f"Bearer {config.api_key}"},
                params={
                    "model": getattr(config, "vlm_model", config.model),
                    "temperature": 0.0,
                    "max_tokens": 8192,
                },
                timeout=180,
            ),
        )
        pipeline_options = VlmPipelineOptions(
            vlm_options=vlm_options,
            enable_remote_services=True,
        )
        format_options = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                pipeline_cls=VlmPipeline,
            ),
        }
    else:
        pipeline_options = PdfPipelineOptions(do_ocr=ocr_engine != "Docling default")
        if ocr_engine == "RapidOCR":
            pipeline_options.ocr_options = RapidOcrOptions(lang=["en"])
        elif ocr_engine == "Tesseract":
            pipeline_options.ocr_options = TesseractOcrOptions(lang=["eng"])
        format_options = {
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
            ),
        }

    if source.suffix.lower() in {
        ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
    }:
        from docling.document_converter import ImageFormatOption

        format_options[InputFormat.IMAGE] = ImageFormatOption(
            pipeline_options=pipeline_options,
        )

    converter = DocumentConverter(format_options=format_options)
    return converter.convert(str(source))


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
    started = time.perf_counter()
    source = Path(file_path).resolve()
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        config = llm_config or AppConfig.from_file()
        if ocr_engine == "Remote VLM":
            config.validate_for_llm(require_vlm=True)
        conversion_engine = (
            "docling_remote_api_vlm"
            if ocr_engine == "Remote VLM"
            else f"docling_{ocr_engine.lower()}"
        )
        debug_prompts: dict[str, Any] = {
            "ocr": {
                "source": (
                    "Docling internal VLM preset"
                    if ocr_engine == "Remote VLM"
                    else "Docling local OCR pipeline"
                ),
                "engine": conversion_engine,
                "model": (
                    getattr(config, "vlm_model", config.model)
                    if ocr_engine == "Remote VLM"
                    else f"{ocr_engine} (lokal)"
                ),
                "prompt": (
                    "Prompt OCR dibuat internal oleh Docling melalui preset "
                    "'granite_vision'; tidak tersedia sebagai string publik "
                    "dari API converter yang digunakan aplikasi."
                    if ocr_engine == "Remote VLM"
                    else (
                        "OCR dijalankan oleh engine lokal Docling; tidak ada "
                        "prompt LLM yang dikirim."
                    )
                ),
                "request_url": (
                    config.base_url.rstrip("/") + "/chat/completions"
                    if ocr_engine == "Remote VLM" and config.base_url
                    else None
                ),
                "parameters": (
                    {
                        "temperature": 0.0,
                        "max_tokens": 8192,
                    }
                    if ocr_engine == "Remote VLM"
                    else {"language": "en" if ocr_engine == "Tesseract" else "en"}
                ),
            }
        }
        converted = _convert_document(source, config, ocr_engine)
        document = converted.document
        markdown = document.export_to_markdown()
        if ocr_engine == "Remote VLM" and not markdown.strip():
            from docling.document_converter import DocumentConverter

            converted = DocumentConverter().convert(str(source))
            document = converted.document
            markdown = document.export_to_markdown()
            conversion_engine = "docling_default_fallback"
        if hasattr(document, "export_to_dict"):
            raw_data = document.export_to_dict()
        elif hasattr(document, "model_dump"):
            raw_data = document.model_dump(mode="json")
        else:
            raw_data = {"note": "Structured JSON unavailable in this Docling version."}

        # A Remote-VLM run that produced nothing fell back to the plain Docling
        # pipeline, so the VLM stage never billed this document.
        if conversion_engine == "docling_default_fallback":
            usage_vlm = _vlm_usage(converted, config, "Docling default", markdown)
        else:
            usage_vlm = _vlm_usage(converted, config, ocr_engine, markdown)

        extraction: dict[str, Any] | None = None
        usage_llm: dict[str, Any] = {}
        raw_ocr_pages = _raw_ocr_pages_from_docling(raw_data, markdown)
        if information_extraction:
            detection = _detect_document_profile(markdown)
            effective_profile = _resolve_extraction_profile(extraction_profile, markdown)
            use_langextract = effective_profile == "Insurance claim"
            legacy_error: Exception | None = None

            if use_langextract:
                try:
                    # The VLM has already produced Markdown. LangExtract gets
                    # that complete text and no page images or keyword filter.
                    remote_result = extract_claim_from_markdown(
                        markdown=markdown,
                        config=config,
                        output_dir=out,
                        output_stem=source.stem,
                    )
                    extraction = remote_result["data"]
                    extraction["pages_analyzed"] = len(raw_ocr_pages) or 1
                    extraction["extraction_engine"] = "langextract"
                    extraction["source_stage"] = "vlm_markdown"
                    extraction["pages_sent_to_remote_vlm"] = []
                    extraction["langextract_artifacts"] = remote_result.get("artifacts", {})
                    usage_llm = remote_result.get("usage", {})
                except Exception as exc:
                    legacy_error = exc
                    if not config.langextract_fallback_to_legacy:
                        raise

            if extraction is None:
                # Explicit opt-in compatibility path for non-claim profiles or
                # a failed LangExtract run. It is not the default claim path.
                schema_model = NATIVE_SCHEMA_REGISTRY[effective_profile]
                page_images = _render_pages(source, analyze_all_pages)
                remote_result = extract_with_remote_vlm(
                    markdown=markdown,
                    page_images=page_images,
                    page_texts=[(page["page_number"], page["raw_text"]) for page in raw_ocr_pages],
                    schema_model=schema_model,
                    config=config,
                )
                extraction = remote_result["data"]
                extraction["pages_analyzed"] = len(page_images)
                extraction["extraction_engine"] = "legacy_remote_vlm"
                extraction["source_stage"] = "legacy_page_vlm"
                extraction["pages_sent_to_remote_vlm"] = [page[0] for page in page_images]
                if legacy_error is not None:
                    extraction["langextract_fallback_reason"] = str(legacy_error)
                extraction["usage"] = remote_result.get("usage", {})
                extraction["cost"] = remote_result.get("cost", {})
                extraction["model"] = remote_result.get("model", config.extraction_model)
                usage_llm = remote_result.get("usage", {})
                debug_prompts["extraction"] = remote_result["debug_prompts"]
            else:
                extraction["detection"] = detection
                extraction["profile_source"] = (
                    "automatic" if extraction_profile == "Auto-detect" else "manual_override"
                )
                extraction["document_conversion_engine"] = conversion_engine
                extraction["schema"] = effective_profile
                extraction["usage"] = remote_result.get("usage", {})
                extraction["cost"] = remote_result.get("cost", {})
                extraction["model"] = remote_result.get("model", config.langextract_model)
                usage_llm = remote_result.get("usage", {})
                debug_prompts["extraction"] = remote_result["debug_prompts"]

        usage_total = combine_usage_reports([usage_vlm, usage_llm], stage="document_total")
        usage_tree = {"vlm_ocr": usage_vlm, "extraction": usage_llm, "total": usage_total}
        # The compact form is the primary artifact; the full auditable contract
        # is kept alongside it so nothing is lost for review or regression.
        claim_form = None
        page_classifications: list[dict[str, Any]] = []
        if raw_ocr_pages:
            page_classifications = classify_pages(raw_ocr_pages, config)
        if isinstance(extraction, dict) and extraction.get("claim_data"):
            extraction = apply_document_claim_type(
                extraction, raw_ocr_pages, page_classifications
            )
        grouped_form = None
        if isinstance(extraction, dict) and extraction.get("claim_data"):
            claim_form = build_claim_form(
                extraction,
                pages=raw_ocr_pages,
                classifications=page_classifications,
            )
            # Slim grouped JSON per screenshot (memberInfo/header/policyInfo)
            # + decisions + provenance ringkas + usage LLM.
            grouped_form = build_full_claim_json(
                {**extraction, "usage": usage_tree},
                pages=raw_ocr_pages,
                classifications=page_classifications,
            )
        data = {
            "claim_form": claim_form,
            "grouped_claim_form": grouped_form,
            "information_extraction": extraction,
            "docling_document": raw_data,
            "raw_ocr_pages": raw_ocr_pages,
            "debug_prompts": debug_prompts,
            "usage": usage_tree,
        }
        markdown_path = out / f"{source.stem}.md"
        json_path = out / f"{source.stem}.json"
        full_json_path = out / f"{source.stem}.full.json"
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        full_json_path.write_text(
            json.dumps(extraction, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return {
            "parser": "Docling",
            "status": "success",
            "input_file": source.name,
            "markdown": markdown,
            "json_data": data,
            "usage": data["usage"],
            "claim_form": claim_form,
            "grouped_claim_form": grouped_form,
            "artifacts": {
                "markdown_path": str(markdown_path),
                "json_path": str(json_path),
                "full_json_path": str(full_json_path),
                "output_directory": str(out),
            },
            "performance": {
                "processing_seconds": round(time.perf_counter() - started, 3)
            },
            "stdout": "",
            "stderr": "",
            "error": None,
        }
    except Exception as exc:
        return {
            "parser": "Docling",
            "status": "error",
            "input_file": source.name,
            "markdown": "",
            "json_data": {},
            "artifacts": {
                "markdown_path": None,
                "json_path": None,
                "output_directory": str(out),
            },
            "performance": {
                "processing_seconds": round(time.perf_counter() - started, 3)
            },
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
