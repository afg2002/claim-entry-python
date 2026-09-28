from __future__ import annotations

import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Type

from openai import OpenAI
from pydantic import BaseModel, create_model

from config import AppConfig
from services.usage_tracker import UsageLedger, build_usage_report


def _usage_value(usage: Any, name: str) -> int:
    """Read one counter from a usage payload that may be a dict or an object.

    ``response.model_dump(mode="json")`` turns the OpenAI SDK object into plain
    dicts, so a ``getattr``-only reader silently reports zero for every run.
    """
    if not usage:
        return 0
    value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _image_content(image_bytes: bytes, mime_type: str) -> dict[str, Any]:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
    }


def _toon_value(value: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    if isinstance(value, list):
        if not value:
            return "[]"
        items = [f"{_toon_value(item, indent + 1)}" for item in value]
        if len(items) == 1:
            return f"[{items[0]}]"
        return "[\n" + ",\n".join(f"{pad}  {item}" for item in items) + f"\n{pad}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        entries = []
        for key, item in value.items():
            entries.append(f"{pad}  {key}: {_toon_value(item, indent + 1)}")
        return "{\n" + ",\n".join(entries) + f"\n{pad}}}"
    return _toon_value(str(value), indent)


def _toy_schema_template() -> dict[str, Any]:
    return {
        "claim_case_type": None,
        "visit_type": None,
        "insured_name": None,
        "provider_name": None,
        "doctor_names": [],
        "diagnosis_primary": None,
        "diagnosis_secondary": [],
        "icd_codes": [],
        "admission_date": None,
        "discharge_date": None,
        "amount_incurred": None,
        "currency": None,
        "medications": [{
            "service_date": None,
            "medicine_name": None,
            "quantity": None,
            "unit": None,
            "doctor_name": None,
            "amount": None,
        }],
        "medicines": [{
            "name": None,
            "dosage": None,
            "date": None,
            "doctor": None,
            "unit": None,
            "frequency": None,
            "duration": None,
            "quantity": None,
            "unit_price": None,
            "total_price": None,
            "source_page": None,
            "source_text": None,
        }],
        "lab_results": [{
            "test_name": None,
            "result": None,
            "unit": None,
            "reference_range": None,
            "abnormal": None,
        }],
        "risk_category": None,
        "risk_percentage": None,
        "potential_risks": [],
        "fraud_indicators": [],
        "pre_existing_indicators": [],
        "investigation_recommended": None,
        "case_snapshot": None,
        "medical_summary": None,
        "financial_summary": None,
        "claim_story": None,
    }


def _dedupe_preserve_order(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _merge_extraction_values(existing: Any, incoming: Any) -> Any:
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    if isinstance(existing, list) and isinstance(incoming, list):
        return _dedupe_preserve_order(existing + incoming)
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for key, value in incoming.items():
            if key in merged:
                merged[key] = _merge_extraction_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    if existing in ("", [], {}, 0, 0.0) and incoming not in ("", [], {}, 0, 0.0):
        return incoming
    return existing if existing is not None else incoming


def _text_only_markdown(markdown: str) -> str:
    """Remove inline base64 images; page images are sent separately."""
    return re.sub(
        r"!\[[^\]]*\]\(data:image/[^)]*\)",
        "[embedded page image omitted; use the supplied page image]",
        markdown,
        flags=re.IGNORECASE,
    )


def _claim_relevant_text(
    text: str,
    *,
    max_chars: int = 5000,
) -> str:
    """
    Preserve local context around relevant lines.

    Unlike the old implementation, this keeps neighboring lines so that
    key/value pairs split across multiple lines are not separated.
    """

    if not text:
        return ""

    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip()
    ]

    keywords = [
        "klaim",
        "claim",
        "polis",
        "policy",
        "policy holder",
        "policyholder",
        "pemegang polis",
        "asuransi",
        "insured",
        "tertanggung",
        "beneficiary",
        "nama",
        "pasien",
        "patient",
        "member",
        "provider",
        "dokter",
        "doctor",
        "tanggal",
        "date",
        "admission",
        "discharge",
        "diagnosa",
        "diagnosis",
        "icd",
        "obat",
        "medicine",
        "medication",
        "invoice",
        "billing",
        "biaya",
        "cost",
        "tagihan",
        "total",
        "lab",
        "hasil",
        "procedure",
        "tindakan",
        "room",
        "consultation",
    ]

    relevant_indexes: set[int] = set()

    for index, line in enumerate(lines):
        lower = line.lower()

        if any(keyword in lower for keyword in keywords):
            # Preserve nearby lines because field values can be
            # placed on the next or previous line.
            for offset in (-2, -1, 0, 1, 2):
                target = index + offset

                if 0 <= target < len(lines):
                    relevant_indexes.add(target)

    selected = [
        lines[index]
        for index in sorted(relevant_indexes)
    ]

    result = "\n".join(selected)

    if len(result) <= max_chars:
        return result

    # If filtering is still too large, preserve the beginning and end.
    head_size = int(max_chars * 0.7)
    tail_size = max_chars - head_size

    return (
        result[:head_size]
        + "\n...[TRUNCATED FOR PROMPT SIZE]...\n"
        + result[-tail_size:]
    )


def _split_large_text(text: str, *, max_chars: int = 3500) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [segment.strip() for segment in re.split(r"\n\s*\n+", text.strip()) if segment.strip()]
    if not paragraphs:
        return [text[:max_chars]]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
        else:
            current = ""
            for part in [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)]:
                if part.strip():
                    chunks.append(part.strip())
    if current:
        chunks.append(current)
    return chunks if chunks else [text[:max_chars]]


def _build_claim_analysis_prompt(markdown: str, page_texts: list[tuple[int, str]] | None = None) -> str:
    cleaned_pages = []
    for page_no, text in (page_texts or []):
        filtered = _claim_relevant_text(text, max_chars=3000)
        if filtered:
            cleaned_pages.append(f"--- PAGE {page_no} ---\n{filtered}")
    pages_block = "\n\n".join(cleaned_pages) or _claim_relevant_text(markdown, max_chars=12000)
    toon_schema = _toon_value(_toy_schema_template(), indent=0)
    return (
        "Anda adalah claim analyst assistant untuk adjudikasi asuransi kesehatan. "
        "Ekstrak seluruh data yang terlihat secara lengkap dan akurat dari dokumen claim. "
        "Tujuan bukan OCR sederhana, tapi structured claim analysis yang langsung bisa dipakai analyst.\n\n"
        "Gunakan TOON (Token-Oriented Object Notation) untuk representasi data di prompt agar lebih ringkas: \n"
        f"{toon_schema}\n\n"
        "HARUS DILAKUKAN:\n"
        "1. Deteksi claim_case_type: NEW, KELENGKAPAN, atau TINJAU_ULANG.\n"
        "2. Deteksi visit_type: OUTPATIENT atau INPATIENT.\n"
        "3. Ekstrak semua dokumen yang ditemukan dan dokumen yang hilang.\n"
        "4. Ekstrak member, provider, doctor, admission/discharge, diagnosis, ICD, medical history, pre-existing indicators.\n"
        "5. Ekstrak semua item obat secara lengkap; jangan ringkas, jangan dedup yang berbeda tanggal/dokter/jumlah.\n"
        "6. Ekstrak semua item billing dan charge.\n"
        "7. Deteksi risk_category: LOW / MEDIUM / HIGH, risk_percentage, potential_risks, fraud_indicators, pre_existing_indicators, investigation_recommended.\n"
        "8. Buat case_snapshot, medical_summary, financial_summary, dan claim_story dalam Bahasa Indonesia.\n"
        "9. Isi missing_information, inconsistencies, analyst_questions berdasarkan fakta di dokumen.\n"
        "10. Gunakan null jika tidak ada data dan array kosong jika tidak ada daftar.\n"
        "11. Jangan mengarang. Jika tidak ditemukan, gunakan null / [] / false sesuai konteks.\n"
        "12. Item obat harus disimpan dengan field seperti service_date, medicine_name, quantity, unit, doctor_name, amount.\n"
        "13. Jika ada duplikasi nama obat dengan tanggal atau halaman berbeda, tetap simpan sebagai record terpisah.\n"
        "14. Untuk output final, jangan hanya ringkas. Simpan data penting seperti obat, diagnosis, ICD, billing, dan risk secara penuh.\n\n"
        "FORMAT OUTPUT: JSON object valid sesuai schema.\n"
        "Dokumen yang harus dibaca:\n"
        + pages_block
    )


def _call_llm_json(
    *,
    client: OpenAI,
    model_name: str,
    messages: list[dict[str, Any]],
    response_model: Type[BaseModel],
) -> tuple[Any, dict[str, Any]]:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": response_model.__name__,
            "strict": True,
            "schema": response_model.model_json_schema(),
        },
    }
    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            response_format=response_format,
        )
    except Exception as first_error:
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                response_format={"type": "json_object"},
            )
        except Exception as fallback_error:
            raise RuntimeError(
                "Remote VLM request failed with structured and JSON modes: "
                f"{fallback_error}"
            ) from first_error

    choice = response.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    content_text = choice.message.content or ""
    if finish_reason == "length" and not content_text.strip():
        raise ValueError(f"Remote VLM stopped because output hit token limit for {response_model.__name__}")
    try:
        parsed = response_model.model_validate_json(content_text)
    except Exception as exc:
        raise ValueError(
            f"Remote VLM returned JSON that does not match {response_model.__name__}: {exc}"
        ) from exc
    return parsed, response.model_dump(mode="json")


def _build_page_extraction_model() -> Type[BaseModel]:
    """
    Lightweight schema for page-level extraction.

    Important:
    Keep this schema focused on factual extraction.
    Do not put risk analysis or narrative summaries here.
    """

    return create_model(
        "PageClaimExtraction",

        # Page metadata
        page_number=(int, ...),
        document_type=(str, ...),

        # Identity
        insured_name=(str | None, None),
        policy_holder_name=(str | None, None),
        policy_number=(str | None, None),
        member_number=(str | None, None),
        patient_name=(str | None, None),
        patient_id=(str | None, None),
        date_of_birth=(str | None, None),
        medical_record_number=(str | None, None),
        address=(str | None, None),

        # Provider / doctor
        provider_name=(str | None, None),
        provider_code=(str | None, None),
        doctor_name=(str | None, None),
        doctor_names=(list[str], []),

        # Claim / visit
        claim_number=(str | None, None),
        claim_case_type=(str | None, None),
        visit_type=(str | None, None),
        admission_date=(str | None, None),
        discharge_date=(str | None, None),
        accident_date=(str | None, None),

        # Medical
        diagnosis_primary=(str | None, None),
        diagnosis_secondary=(list[str], []),
        diagnosis=(list[str], []),
        icd_codes=(list[str], []),
        medical_history=(list[str], []),
        procedures=(list[str], []),
        treatments=(list[str], []),

        # Medication / laboratory
        medications=(list[dict[str, Any]], []),
        medicines=(list[dict[str, Any]], []),
        lab_results=(list[dict[str, Any]], []),

        # Financial
        amount_incurred=(float | None, None),
        currency=(str | None, None),
        room_charge=(float | None, None),
        medicine_charge=(float | None, None),
        consultation_charge=(float | None, None),
        procedure_charge=(float | None, None),
        lab_charge=(float | None, None),
        radiology_charge=(float | None, None),

        # Invoice
        invoice_number=(str | None, None),
        invoice_date=(str | None, None),
        subtotal=(float | None, None),
        tax=(float | None, None),
        total_amount=(float | None, None),

        # Supporting documents
        documents_found=(list[str], []),
        documents_missing=(list[str], []),

        # Traceability
        source_pages=(list[int], []),
        notes=(list[str], []),
    )

def _extract_single_page(
    *,
    client: OpenAI,
    model_name: str,
    page_number: int,
    page_text: str,
    page_image: tuple[int, bytes, str] | None,
    schema_model: Type[BaseModel],
) -> dict[str, Any]:
    page_chunks = _split_large_text(page_text, max_chars=3500)
    prompt_text = (
    page_text
    if len(page_text) <= 5000
    else _claim_relevant_text(page_text, max_chars=5000)
    )
    page_model = schema_model if schema_model.__name__ not in {"ClaimAnalysis", "InsuranceClaim"} else _build_page_extraction_model()

    if len(page_chunks) == 1:
        prompt = (
            f"Ekstrak hanya fakta claim yang relevan pada halaman {page_number}. "
            "Fokus ke identitas peserta, provider/dokter, tanggal, diagnosis, obat, billing, lab, prosedur, dan risiko. "
            "Jangan menambahkan informasi yang tidak ada di dokumen. Jangan deduplicate item berbeda tanggal, dokter, atau jumlah. "
            "Gunakan null atau array kosong jika tidak ada.\n\n"
            f"TEKS HALAMAN {page_number}:\n{prompt_text}"
        )
        message_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if page_image is not None:
            _, image_bytes, mime_type = page_image
            message_content.extend(
                [
                    {"type": "text", "text": f"SOURCE PAGE {page_number}"},
                    _image_content(image_bytes, mime_type),
                ]
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "Anda adalah sistem document understanding yang fokus pada satu halaman. "
                    "Kembalikan data claim yang relevan, jangan berlebihan, jangan mengarang, "
                    "dan jangan mengirim raw OCR / raw_response ke langkah berikutnya."
                ),
            },
            {"role": "user", "content": message_content},
        ]
    else:
        chunk_parts: list[dict[str, Any]] = []
        for index, chunk in enumerate(page_chunks, start=1):
            chunk_text = _claim_relevant_text(chunk, max_chars=2000)
            prompt = (
                f"Ekstrak fakta claim pada potongan {index}/{len(page_chunks)} halaman {page_number}. "
                "Fokus pada identitas, provider, diagnosis, obat, billing, lab, tanggal, dan risiko. "
                "Jangan menggabungkan dengan potongan lain; hasil akan digabung di fase akhir.\n\n"
                f"TEKS POTONGAN {index}/{len(page_chunks)}\n{chunk_text}"
            )
            chunk_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            if page_image is not None:
                _, image_bytes, mime_type = page_image
                chunk_content.extend(
                    [
                        {"type": "text", "text": f"SOURCE PAGE {page_number}"},
                        _image_content(image_bytes, mime_type),
                    ]
                )
            chunk_parts.append({"role": "user", "content": chunk_content})
        messages = [
            {
                "role": "system",
                "content": (
                    "Anda memproses potongan text halaman yang besar. Kembalikan data claim yang relevan dari potongan ini saja, "
                    "tanpa mengarang, tanpa raw OCR, dan tanpa deduplicate data yang berbeda tanggal/doctor/page."
                ),
            },
            *chunk_parts,
        ]

    parsed, raw_response = _call_llm_json(
        client=client,
        model_name=model_name,
        messages=messages,
        response_model=page_model,
    )
    payload = parsed.model_dump(mode="json")
    return {
        "page_number": page_number,
        "document_type": payload.get("document_type") or "",
        "extracted": payload,
        # Kept so the caller can total the real per-page cost instead of
        # reporting zeros for every page of a multi-page document.
        "usage": raw_response.get("usage", {}),
    }


def _merge_page_results(page_results: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for page_result in page_results:
        extracted = page_result.get("extracted", {}) or {}
        merged = _merge_extraction_values(merged, extracted)
    return merged


def _finalize_claim_analysis(
    *,
    client: OpenAI,
    model_name: str,
    merged_payload: dict[str, Any],
    schema_model: Type[BaseModel],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the finalized payload plus the usage block for this call."""
    if schema_model.__name__ not in {"ClaimAnalysis", "InsuranceClaim"}:
        return merged_payload, {}

    final_prompt = (
        "Terjemahkan hasil gabungan halaman berikut ke format ClaimAnalysis final. "
        "Jangan menambahkan data di luar dokumen. Gunakan array kosong untuk yang tidak ada. "
        "Untuk obat, simpan tiap record berbeda berdasarkan tanggal, dokter, kuantitas, atau halaman.\n\n"
        f"MERGED_PAYLOAD:\n{_toon_value(merged_payload, indent=0)}"
    )
    messages = [
        {
            "role": "system",
            "content": (
                "Anda adalah analyst claim insurance. Ambil fakta hanya dari MERGED_PAYLOAD. "
                "Jangan mengembalikan raw OCR atau JSON yang tidak valid. "
                "Semua obat dan billing harus tetap dipertahankan jika berbeda date/page/doctor."
            ),
        },
        {"role": "user", "content": [{"type": "text", "text": final_prompt}]},
    ]
    parsed, raw_response = _call_llm_json(
        client=client,
        model_name=model_name,
        messages=messages,
        response_model=schema_model,
    )
    return parsed.model_dump(mode="json"), raw_response.get("usage", {})


def _normalize_claim_output(data: dict[str, Any], *, source_pages: list[int] | None = None) -> dict[str, Any]:
    """Separate factual extraction and analyst analysis while preserving compatibility."""
    if not isinstance(data, dict):
        data = {}

    claim_data = dict(data)
    for key in [
        "analyst_analysis",
        "provenance",
        "claim_data",
    ]:
        claim_data.pop(key, None)

    analyst_payload = {
        "visit_type": {
            "value": data.get("visit_type"),
            "status": "derived" if data.get("visit_type") else "unknown",
            "confidence": None,
            "evidence": [],
        },
        "missing_information": list(data.get("missing_information", []) or []),
        "inconsistencies": list(data.get("inconsistencies", []) or []),
        "potential_risks": list(data.get("potential_risks", []) or []),
        "fraud_indicators": list(data.get("fraud_indicators", []) or []),
        "investigation_recommended": bool(data.get("investigation_recommended", False)),
        "case_snapshot": data.get("case_snapshot"),
        "medical_summary": data.get("medical_summary"),
        "financial_summary": data.get("financial_summary"),
        "claim_story": data.get("claim_story"),
    }
    if isinstance(data.get("analyst_analysis"), dict):
        analyst_payload.update(data["analyst_analysis"])

    normalized = {
        "claim_data": claim_data,
        "analyst_analysis": analyst_payload,
        "provenance": {
            "source_pages": list(source_pages or []),
            "source_text_available": True,
        },
        "combined_extraction": claim_data,
        "missing_information": analyst_payload["missing_information"],
        "inconsistencies": analyst_payload["inconsistencies"],
        "analyst_questions": list(data.get("analyst_questions", []) or []),
        "documents_found": list(data.get("documents_found", []) or []),
        "documents_missing": list(data.get("documents_missing", []) or []),
    }

    # Preserve compatibility with old UI and downstream code.
    for key in [
        "document_type",
        "summary",
        "analyst_notes",
        "pages_analyzed",
        "detection",
        "profile_source",
        "schema",
        "extraction_engine",
        "document_conversion_engine",
        "usage",
        "cost",
        "model",
    ]:
        if key in data:
            normalized[key] = data[key]

    return normalized


def _legacy_usage_report(
    ledger: UsageLedger,
    config: AppConfig,
    model_name: str,
    page_count: int,
) -> dict[str, Any]:
    """Build the usage report for the legacy page-VLM extraction path."""
    note = None
    if not ledger.has_provider_usage:
        note = (
            f"Provider tidak mengirim blok usage untuk {page_count} halaman; "
            "angka berasal dari estimasi tiktoken dan bukan tagihan aktual."
        )
    return build_usage_report(
        ledger,
        stage="legacy_page_vlm",
        model=model_name,
        provider="openai_compatible",
        usd_to_idr=config.usd_to_idr,
        input_rate_per_million=config.input_rate_per_million,
        output_rate_per_million=config.output_rate_per_million,
        note=note,
    )


def extract_with_remote_vlm(
    *,
    markdown: str,
    page_images: list[tuple[int, bytes, str]],
    schema_model: Type[BaseModel],
    config: AppConfig,
    page_texts: list[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    config.validate_for_llm(require_extraction=True)
    client = OpenAI(api_key=config.api_key, base_url=config.base_url)
    model_name = getattr(config, "extraction_model", config.model)

    if page_texts and len(page_texts) > 0:
        page_lookup = {
            page_number: (page_number, image_bytes, mime_type)
            for page_number, image_bytes, mime_type in page_images
        }

        max_workers = min(4, max(1, len(page_texts)))
        page_results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(
                    _extract_single_page,
                    client=client,
                    model_name=model_name,
                    page_number=page_number,
                    page_text=page_text,
                    page_image=page_lookup.get(page_number),
                    schema_model=schema_model,
                )
                for page_number, page_text in page_texts
            ]
            for future in as_completed(futures):
                page_results.append(future.result())

        page_results.sort(key=lambda item: item.get("page_number", 0))
        merged_payload = _merge_page_results(page_results)
        # Total every page call plus the finalize call; previously this branch
        # hardcoded zeros, so a multi-page document always looked free.
        ledger = UsageLedger()
        for item in page_results:
            ledger.record(item.get("usage", {}))
        if not ledger.has_provider_usage and config.usage_estimate_fallback:
            ledger.estimate(
                input_text="\n\n".join(
                    f"PAGE {item['page_number']}\n{item.get('extracted')}"
                    for item in page_results
                ),
                requests=ledger.requests or 1,
            )



        if schema_model.__name__ in {"ClaimAnalysis", "InsuranceClaim"}:
            final_data, finalize_usage = _finalize_claim_analysis(
                client=client,
                model_name=model_name,
                merged_payload=merged_payload,
                schema_model=schema_model,
            )
            ledger.record(finalize_usage)
            normalized = _normalize_claim_output(
                final_data,
                source_pages=[item["page_number"] for item in page_results],
            )
            usage = _legacy_usage_report(ledger, config, model_name, len(page_results))
            return {
                "model": model_name,
                "data": normalized,
                "debug_prompts": {
                    "pages_processed": [item["page_number"] for item in page_results],
                    "page_count": len(page_results),
                    "merge_mode": "deterministic_python_merge",
                },
                "usage": usage,
                "cost": usage["cost"],
                "raw_response": {},
            }

        normalized = _normalize_claim_output(
            merged_payload,
            source_pages=[item["page_number"] for item in page_results],
        )
        usage = _legacy_usage_report(ledger, config, model_name, len(page_results))
        return {
            "model": model_name,
            "data": normalized,
            "debug_prompts": {
                "pages_processed": [item["page_number"] for item in page_results],
                "page_count": len(page_results),
                "merge_mode": "deterministic_python_merge",
            },
            "usage": usage,
            "cost": usage["cost"],
            "raw_response": {},
        }

    text_markdown = _text_only_markdown(markdown)
    if schema_model.__name__ in {"ClaimAnalysis", "InsuranceClaim"}:
        prompt = _build_claim_analysis_prompt(text_markdown)
    else:
        prompt = (
            "Analisis dokumen ini. Kembalikan object JSON yang valid dengan schema yang diminta. "
            "Gunakan struktur yang ringkas dan fokus pada fakta yang relevan.\n\n"
            + text_markdown[:12000]
        )

    payload = [{"type": "text", "text": prompt}]
    for page_number, image_bytes, mime_type in page_images:
        payload.extend([
            {"type": "text", "text": f"SOURCE PAGE {page_number}"},
            _image_content(image_bytes, mime_type),
        ])

    messages = [
        {"role": "system", "content": "Anda adalah sistem document understanding yang fokus pada fakta yang relevan dan menghindari output terlalu besar."},
        {"role": "user", "content": payload},
    ]
    parsed, raw_response = _call_llm_json(
        client=client,
        model_name=model_name,
        messages=messages,
        response_model=schema_model,
    )

    data = parsed.model_dump(mode="json")
    if schema_model.__name__ in {"ClaimAnalysis", "InsuranceClaim"}:
        data = _normalize_claim_output(data, source_pages=[page_number for page_number, _, _ in page_images])

    ledger = UsageLedger()
    ledger.record(raw_response.get("usage", {}))
    if not ledger.has_provider_usage and config.usage_estimate_fallback:
        # Page images are not tokenizable, so only the text side is estimated.
        ledger.estimate(
            input_text=prompt,
            output_text=json.dumps(data, ensure_ascii=False, default=str),
            requests=1,
        )
    usage = _legacy_usage_report(ledger, config, model_name, len(page_images) or 1)
    return {
        "model": model_name,
        "data": data,
        "debug_prompts": {"user_text": prompt, "image_pages": [page_number for page_number, _, _ in page_images]},
        "usage": usage,
        "cost": usage["cost"],
        "raw_response": raw_response,
    }
