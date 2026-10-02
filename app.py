from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import fitz
import pdf_inspector
import pandas as pd
import streamlit as st
from config import AppConfig
from comparison import compare_results, similarity_matrix
from comparison.metrics import usage_stage_reports
from parsers import parse_docling, parse_mineru
from services.claim_form import refresh_grouped_decisions
from services.usage_tracker import combine_usage_reports
from utils import (
    PARSER_FORMAT_SUPPORT,
    SUPPORTED_UPLOAD_TYPES,
    create_safe_internal_filename,
    get_compatible_parsers,
    get_file_extension,
    get_incompatible_parsers,
    is_image_file,
)
from utils.formatting import format_amount


APP_TITLE = "Document Parser Comparison Lab"
PARSER_NAMES = ["Docling", "MinerU"]
LLM_CONFIG = AppConfig.from_file()

USAGE_SOURCE_LABELS = {
    "provider": "Provider",
    "estimated": "Estimasi",
    "mixed": "Campuran",
    "none": "Tidak ada",
}
USAGE_STAGE_LABELS = {
    # Keys match the `stage` value each report carries, not the JSON tree key.
    "vlm_ocr": "VLM / OCR",
    "langextract": "LLM extraction",
    "legacy_page_vlm": "LLM extraction (legacy)",
    "icd_suggestion": "ICD suggestion",
    "document_total": "Total dokumen",
}


def _render_grouped_claim(grouped: dict[str, Any]) -> None:
    """Render slim grouped JSON per screenshot (memberInfo/header/policyInfo).

    Setiap parent hanya dirender bila ada (yang ngga masuk gausah ada
    parentnya). Key memakai nama editForm agar siap autofill.
    """
    flags = st.columns(6)
    flags[0].metric("New claim", str(bool(grouped.get("isNewClaim"))))
    flags[1].metric("Kelengkapan", str(bool(grouped.get("isKelengkapan"))))
    flags[2].metric("RI", str(bool(grouped.get("isRI"))))
    flags[3].metric("RJ", str(bool(grouped.get("isRJ"))))
    flags[4].metric("Overseas", str(bool(grouped.get("isOverseas"))))
    flags[5].metric("Possible new", str(bool(grouped.get("possibleNewClaim"))))
    for conf_key, conf_label in (
        ("riConfidence", "RI confidence"),
        ("rjConfidence", "RJ confidence"),
    ):
        if grouped.get(conf_key) is not None:
            st.progress(
                min(100, max(0, int(float(grouped[conf_key])))),
                text=f"{conf_label}: {grouped[conf_key]}",
            )

    for decision_name, decision in (grouped.get("decisions") or {}).items():
        if not isinstance(decision, dict):
            continue
        with st.expander(
            f"Keputusan {decision_name}: {decision.get('value', '-')}", expanded=False
        ):
            st.write(f"**Reason:** {decision.get('reason') or '-'}")
            st.caption(f"Method: `{decision.get('method') or '-'}`")
            if decision.get("evidence"):
                st.caption("Evidence: " + " | ".join(map(str, decision["evidence"])))
            if decision.get("confidence") is not None:
                st.caption(f"Confidence: {decision.get('confidence')}")

    if grouped.get("memberInfo"):
        st.markdown("**MEMBER INFO**")
        st.json(grouped["memberInfo"], expanded=True)
    if grouped.get("header"):
        st.markdown("**HEADER**")
        st.json(grouped["header"], expanded=True)
    if grouped.get("policyInfo"):
        st.markdown("**POLICY**")
        st.json(grouped["policyInfo"], expanded=True)
    if grouped.get("serviceLines"):
        st.markdown("**SERVICE LINES**")
        st.dataframe(
            pd.DataFrame(grouped["serviceLines"]),
            use_container_width=True,
            hide_index=True,
        )
    if grouped.get("provenance"):
        with st.expander("Provenance ringkas"):
            st.json(grouped["provenance"], expanded=False)
    review = grouped.get("review") or {}
    attention = review.get("needsAttention") or []
    missing = review.get("missingFields") or []
    if attention:
        st.markdown("**Perlu perhatian**")
        for item in attention:
            st.warning(str(item))
    if missing:
        st.caption("Field wajib yang belum ditemukan: " + ", ".join(map(str, missing)))
    usage = grouped.get("usage") or {}
    if usage:
        with st.expander("Usage LLM (per tahap)"):
            st.json(usage, expanded=False)

    with st.expander("JSON grouped lengkap (siap autofill editForm)"):
        st.json(grouped, expanded=False)


def _render_claim_form(claim_form: dict[str, Any]) -> None:
    """Render the compact claim JSON as readable sections.

    Values whose status is ``default`` were supplied by the form rules rather
    than read from the document, so they are labelled to avoid being mistaken
    for extracted facts.
    """
    document = claim_form.get("document") or {}
    pages = document.get("pages") or []
    if pages:
        st.markdown("**Dokumen per halaman**")
        page_rows = [
            {
                "Halaman": page.get("page"),
                # The model's own name for the page, never a bucket label.
                "Jenis dokumen": page.get("document_type") or "(belum dikenali)",
                "Kategori": page.get("category") or "-",
                "Judul terbaca": page.get("label") or "-",
            }
            for page in pages
        ]
        st.dataframe(pd.DataFrame(page_rows), use_container_width=True, hide_index=True)

    visit = claim_form.get("visit") or {}
    if visit:
        st.markdown("**Kunjungan**")
        visit_columns = st.columns(5)
        visit_columns[0].metric("Tipe", str(visit.get("type_of_visit") or "unknown"))
        visit_columns[1].metric(
            "Tanggal layanan", str(visit.get("hospitalization_date") or "-")
        )
        visit_columns[2].metric(
            "Masuk", str(visit.get("admission_date") or "-")
        )
        visit_columns[3].metric(
            "Keluar", str(visit.get("discharge_date") or "-")
        )
        visit_columns[4].metric("Hari", str(visit.get("no_of_days") or 0))
        st.caption(
            f"Status: `{visit.get('status', 'unknown')}` · "
            f"Metode: `{visit.get('method') or '-'}`"
        )
        if visit.get("evidence"):
            st.caption("Bukti: " + " | ".join(str(item) for item in visit["evidence"]))
        if visit.get("note"):
            st.warning(str(visit["note"]))

    member = claim_form.get("member_info") or {}
    policy = claim_form.get("policy_info") or {}
    if member or policy:
        st.markdown("**Peserta dan polis**")
        st.json(
            {"member_info": member, "policy_info": policy},
            expanded=True,
        )

    claim = claim_form.get("claim") or {}
    provider = claim_form.get("provider") or {}
    if claim or provider:
        st.markdown("**Header klaim dan provider**")
        st.json({"claim": claim, "provider": provider}, expanded=True)

    medical = claim_form.get("medical") or {}
    if medical:
        st.markdown("**Medis**")
        st.write("**Reason of visit:**", medical.get("reason_of_visit") or "-")
        st.caption(f"Status alasan: `{medical.get('reason_status', 'unknown')}`")
        if medical.get("reason_note"):
            st.caption(str(medical["reason_note"]))
        if medical.get("icd_codes"):
            st.write("**ICD codes:**", ", ".join(str(code) for code in medical["icd_codes"]))
        recommendations = medical.get("icd_recommendations") or []
        if recommendations:
            st.warning(
                "Rekomendasi ICD BUKAN fakta dokumen dan memerlukan validasi coder."
            )
            st.json(recommendations, expanded=False)
        for key, label in (
            ("diagnosis", "Diagnosis"),
            ("medical_history", "Riwayat penyakit"),
            ("pre_existing_conditions", "Pre-existing"),
        ):
            if medical.get(key):
                st.write(f"**{label}:**", ", ".join(str(item) for item in medical[key]))

    financial = claim_form.get("financial") or {}
    if financial:
        st.markdown("**Finansial**")
        financial_columns = st.columns(4)
        financial_columns[0].metric(
            "Amount incurred", format_amount(financial.get("amount_incurred"))
        )
        financial_columns[1].metric(
            "Currency",
            f"{financial.get('currency')} ({financial.get('currency_status')})",
        )
        financial_columns[2].metric(
            "Exchange rate",
            f"{financial.get('exchange_rate')} ({financial.get('exchange_rate_status')})",
        )
        financial_columns[3].metric(
            "Total", format_amount(financial.get("total_amount"))
        )
        if financial.get("currency_status") == "default":
            st.caption(
                "Mata uang dan kurs memakai default formulir (IDR, 1) karena "
                "dokumen tidak menyatakannya."
            )

    for key, label in (
        ("service_lines", "Baris layanan"),
        ("medications", "Obat"),
        ("procedures", "Tindakan"),
        ("lab_results", "Hasil laboratorium"),
    ):
        rows = claim_form.get(key) or []
        if rows:
            st.markdown(f"**{label}**")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    review = claim_form.get("review") or {}
    attention = review.get("needs_attention") or []
    missing = review.get("missing_information") or []
    if attention:
        st.markdown("**Perlu perhatian**")
        for item in attention:
            st.warning(str(item))
    if missing:
        st.caption("Field wajib yang belum ditemukan: " + ", ".join(str(i) for i in missing))

    with st.expander("JSON claim form lengkap"):
        st.json(claim_form, expanded=False)


def _usage_cost(usage: dict[str, Any], cost: Any = None) -> dict[str, float]:
    """Resolve a cost dict from either the usage report or a legacy value."""
    if isinstance(cost, dict) and cost:
        return {
            "usd": float(cost.get("usd", 0) or 0),
            "idr": float(cost.get("idr", 0) or 0),
        }
    usage_cost = usage.get("cost")
    if isinstance(usage_cost, dict) and usage_cost:
        return {
            "usd": float(usage_cost.get("usd", 0) or 0),
            "idr": float(usage_cost.get("idr", 0) or 0),
        }
    return {"usd": float(cost or 0), "idr": float(cost or 0) * LLM_CONFIG.usd_to_idr}


def _render_usage_metrics(usage: dict[str, Any], cost: Any = None) -> None:
    """Show the token counters for one stage, flagging the measurement source.

    Estimates are never presented as if they were billed usage, so the source
    badge is always visible next to the numbers.
    """
    source = str(usage.get("source", "provider" if usage.get("available") else "none"))
    badge = f"Sumber: {USAGE_SOURCE_LABELS.get(source, source)}"
    if source in {"estimated", "mixed"}:
        st.caption(f"{badge} — angka estimasi bukan tagihan aktual.")
    else:
        st.caption(badge)

    cost_dict = _usage_cost(usage, cost)
    columns = st.columns(7)
    columns[0].metric("Input tokens", f"{int(usage.get('input_tokens', 0) or 0):,}")
    columns[1].metric("Output tokens", f"{int(usage.get('output_tokens', 0) or 0):,}")
    columns[2].metric("Reasoning tokens", f"{int(usage.get('reasoning_tokens', 0) or 0):,}")
    columns[3].metric("Total tokens", f"{int(usage.get('total_tokens', 0) or 0):,}")
    columns[4].metric("Requests", f"{int(usage.get('requests', 0) or 0):,}")
    columns[5].metric("Cost USD", f"${cost_dict['usd']:.6f}")
    columns[6].metric("Cost IDR", f"Rp {cost_dict['idr']:,.2f}")

    if usage.get("note"):
        st.caption(str(usage["note"]))



def inspect_pdf_type(pdf_path: str):
    try:
        result = pdf_inspector.classify_pdf(pdf_path)

        return {
            "status": "success",
            "raw": result,
        }

    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
        }


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="📄",
    layout="wide",
)

st.title("📄 Document Parser Comparison Lab")
st.caption(
    "Docling information extraction untuk analisis dokumen tingkat penuh, "
    "dengan hasil JSON terstruktur dan review risiko."
)

# -----------------------------------------------------------------------------
# Session state
# -----------------------------------------------------------------------------
if "results" not in st.session_state:
    st.session_state.results = []

if "job_dir" not in st.session_state:
    st.session_state.job_dir = None

if "original_filename" not in st.session_state:
    st.session_state.original_filename = None


# -----------------------------------------------------------------------------
# Sidebar configuration
# -----------------------------------------------------------------------------
with st.sidebar:
    st.header("Konfigurasi")

    selected_parsers = st.multiselect(
        "Parser PDF/Markdown",
        options=PARSER_NAMES,
        default=["Docling"],
        help=(
            "Docling Remote VLM menghasilkan Markdown lewat API. MinerU "
            "menjalankan pipeline lokal biasa; tidak memakai VLM/API MinerU."
        ),
    )
    st.caption(
        "Pilih satu atau bandingkan keduanya. MinerU hanya memakai backend "
        "lokal `pipeline`."
    )

    ocr_engine = st.selectbox(
        "Docling OCR engine",
        options=["Remote VLM", "RapidOCR", "Tesseract", "Docling default"],
        index=0,
        disabled="Docling" not in selected_parsers,
        help=(
            "Remote VLM memakai API gateway. RapidOCR dan Tesseract berjalan "
            "lokal melalui pipeline PDF Docling. Opsi ini tidak memengaruhi "
            "MinerU."
        ),
    )
    mineru_backend = st.selectbox(
        "MinerU backend",
        options=["pipeline"],
        index=0,
        disabled="MinerU" not in selected_parsers,
        help="MinerU lokal; tidak ada VLM/API pada pilihan ini.",
    )

    st.subheader("Model aktif")
    if ocr_engine == "Remote VLM":
        vlm_model = getattr(LLM_CONFIG, "vlm_model", LLM_CONFIG.model)
        st.write(f"**OCR/VLM model:** `{vlm_model or 'belum diatur'}`")
        st.caption(
            "Remote VLM dipakai untuk konversi OCR dan Markdown. "
            "API key dibaca dari config.properties dan tidak ditampilkan."
        )
    elif ocr_engine == "RapidOCR":
        st.write("**OCR model:** `RapidOCR (lokal)`")
    elif ocr_engine == "Tesseract":
        st.write("**OCR model:** `Tesseract (lokal, bahasa: eng)`")
    else:
        st.write("**OCR model:** `Tidak ada OCR tambahan`")

    extraction_model = getattr(
        LLM_CONFIG,
        "extraction_model",
        LLM_CONFIG.model,
    )
    st.write(
        f"**Legacy extraction model:** `{extraction_model or 'belum diatur'}`"
    )
    langextract_model = getattr(LLM_CONFIG, "langextract_model", "")
    langextract_provider = getattr(LLM_CONFIG, "langextract_provider", "openai")
    st.write(
        f"**LangExtract:** `{langextract_provider} / "
        f"{langextract_model or 'belum diatur'}`"
    )
    st.caption(
        "Default claim pipeline: VLM hanya membuat Markdown; LangExtract "
        "mengekstrak item facts dari Markdown. Legacy VLM extraction "
        "hanya dipakai untuk fallback eksplisit/non-claim."
    )

    docling_information_extraction = st.toggle(
        "Information extraction (LangExtract)",
        value=True,
        help=(
            "Untuk dokumen claim, jalankan LangExtract pada Markdown hasil "
            "VLM. Non-claim/legacy dapat dikonfigurasi terpisah."
        ),
    )

    analyze_all_pages = st.toggle(
        "Analisis seluruh halaman",
        value=True,
        disabled=not docling_information_extraction,
        help=(
            "Gabungkan seluruh halaman menjadi satu insight dokumen. "
            "Jika dimatikan, hanya halaman pertama yang dianalisis."
        ),
    )

    st.divider()
    extraction_profile = st.selectbox(
        "Jenis dokumen",
        options=[
            "Insurance claim",
            "Auto-detect",
            "Invoice",
            "Medical document",
            "General document",
        ],
        index=0,
        help=(
            "Workflow ini dirancang untuk claim analysis. "
            "Default tetap memakai Insurance claim agar output schema konsisten."
        ),
    )


# -----------------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------------
uploaded = st.file_uploader(
    "Upload dokumen",
    type=SUPPORTED_UPLOAD_TYPES,
    accept_multiple_files=False,
    help=(
        "PDF dapat dibandingkan dengan Docling dan MinerU. Office dan "
        "gambar mengikuti dukungan format masing-masing parser."
    ),
)


if uploaded is not None:
    file_bytes = uploaded.getvalue()
    extension = get_file_extension(uploaded.name)
    pdf_inspector_result = None

    if extension == "pdf":
        tmp_pdf = tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
        )

        tmp_pdf.write(file_bytes)
        tmp_pdf.close()

        pdf_inspector_result = inspect_pdf_type(
            tmp_pdf.name
        )
    compatible_parsers = get_compatible_parsers(uploaded.name)
    incompatible_parsers = get_incompatible_parsers(uploaded.name)

    effective_parsers = [
        parser_name
        for parser_name in selected_parsers
        if parser_name in compatible_parsers
    ]

    unsupported_selected = [
        parser_name
        for parser_name in selected_parsers
        if parser_name not in compatible_parsers
    ]

    preview_col, info_col = st.columns([2, 1])
    page_count: int | None = None

    with preview_col:
        st.subheader("Preview")

        if extension == "pdf":
            try:
                pdf_document = fitz.open(
                    stream=file_bytes,
                    filetype="pdf",
                )
                page_count = pdf_document.page_count

                if page_count > 0:
                    pixmap = pdf_document[0].get_pixmap(
                        matrix=fitz.Matrix(1.3, 1.3),
                        alpha=False,
                    )
                    st.image(
                        pixmap.tobytes("png"),
                        caption="Halaman pertama",
                        use_container_width=True,
                    )
                else:
                    st.warning("PDF tidak memiliki halaman.")

                pdf_document.close()
            except Exception as exc:
                st.warning(f"Preview PDF gagal: {exc}")

        elif is_image_file(uploaded.name):
            st.image(
                file_bytes,
                caption=uploaded.name,
                use_container_width=True,
            )

        else:
            st.info(
                "Preview visual belum tersedia untuk format "
                f"`{extension.upper()}`. File tetap dapat diproses oleh "
                "parser yang kompatibel."
            )

    with info_col:
        if pdf_inspector_result:
            st.subheader("PDF Inspection")

            if pdf_inspector_result["status"] == "success":

                st.write(pdf_inspector_result)

            else:
                st.warning(
                    f"PDF Inspector gagal: "
                    f"{pdf_inspector_result['error']}"
                )
        st.subheader("Informasi dokumen")
        st.write(f"**Nama:** {uploaded.name}")
        st.write(f"**Format:** `{extension.upper()}`")
        st.write(f"**Ukuran:** {len(file_bytes):,} byte")

        if page_count is not None:
            st.write(f"**Halaman:** {page_count}")

        st.write("**Parser kompatibel:**")
        for parser_name in compatible_parsers:
            st.write(f"✅ {parser_name}")

        for parser_name in incompatible_parsers:
            st.write(f"⛔ {parser_name}")

    if unsupported_selected:
        st.warning(
            "Parser berikut tidak mendukung format "
            f"`{extension.upper()}` dan akan dilewati: "
            + ", ".join(unsupported_selected)
        )

    if not selected_parsers:
        st.warning("Pilih minimal satu parser pada sidebar.")

    if selected_parsers and not effective_parsers:
        st.error(
            "Tidak ada parser terpilih yang mendukung format dokumen ini."
        )

    run_disabled = not selected_parsers or not effective_parsers

    if st.button(
        "🚀 Jalankan perbandingan",
        type="primary",
        disabled=run_disabled,
    ):
        original_suffix = Path(uploaded.name).suffix.lower()

        if not original_suffix:
            st.error(
                "File tidak memiliki ekstensi sehingga formatnya tidak "
                "dapat diidentifikasi."
            )
            st.stop()

        job_dir = Path(tempfile.mkdtemp(prefix="pc_"))

        try:
            internal_filename = create_safe_internal_filename(uploaded.name)
        except ValueError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            st.error(str(exc))
            st.stop()

        input_path = job_dir / internal_filename
        input_path.write_bytes(file_bytes)

        st.caption(
            "Nama internal aman untuk Windows: "
            f"`{input_path.name}`"
        )

        progress_bar = st.progress(
            0.0,
            text="Menyiapkan parser...",
        )
        status_box = st.status(
            "Menyiapkan proses...",
            expanded=True,
        )
        results: list[dict[str, Any]] = []
        parser_count = len(effective_parsers)
        current_parser_index = 0

        for current_parser_index, parser_name in enumerate(
            effective_parsers
        ):
            status_box.update(
                label=f"Menjalankan {parser_name}...",
                state="running",
                expanded=True,
            )
            progress_bar.progress(
                current_parser_index / parser_count,
                text=f"Menjalankan {parser_name}...",
            )

            if parser_name == "Docling":
                result = parse_docling(
                    input_path,
                    job_dir / "dc",
                    information_extraction=docling_information_extraction,
                    analyze_all_pages=analyze_all_pages,
                    extraction_profile=extraction_profile,
                    llm_config=LLM_CONFIG,
                    ocr_engine=ocr_engine,
                )

            elif parser_name == "MinerU":
                result = parse_mineru(
                    input_path,
                    job_dir / "mineru",
                    backend=mineru_backend,
                    information_extraction=docling_information_extraction,
                    llm_config=LLM_CONFIG,
                )
            else:
                continue

            results.append(result)

            completed_progress = (
                current_parser_index + 1
            ) / parser_count
            progress_bar.progress(
                completed_progress,
                text=(
                    f"{parser_name} selesai"
                    if result.get("status") == "success"
                    else f"{parser_name} gagal"
                ),
            )

            if result.get("status") == "success":
                seconds = result.get(
                    "performance",
                    {},
                ).get("processing_seconds", 0)
                status_box.write(
                    f"✅ {parser_name} selesai dalam "
                    f"{float(seconds):.1f} detik."
                )
            else:
                status_box.write(
                    f"❌ {parser_name}: "
                    f"{result.get('error', 'Unknown error')}"
                )

        success_count = sum(
            result.get("status") == "success"
            for result in results
        )

        if success_count == len(results):
            status_box.update(
                label="Semua parser selesai.",
                state="complete",
                expanded=False,
            )
        elif success_count > 0:
            status_box.update(
                label=(
                    f"{success_count}/{len(results)} parser berhasil."
                ),
                state="complete",
                expanded=True,
            )
        else:
            status_box.update(
                label="Semua parser gagal.",
                state="error",
                expanded=True,
            )

        progress_bar.progress(
            1.0,
            text="Proses perbandingan selesai.",
        )

        st.session_state.results = results
        st.session_state.job_dir = str(job_dir)
        st.session_state.original_filename = uploaded.name


# -----------------------------------------------------------------------------
# Results
# -----------------------------------------------------------------------------
results = st.session_state.results

if results:
    st.divider()
    st.subheader("Ringkasan benchmark")

    if st.session_state.original_filename:
        st.caption(
            f"Dokumen: {st.session_state.original_filename}"
        )

    metrics_dataframe = compare_results(results)
    st.dataframe(
        metrics_dataframe,
        use_container_width=True,
        hide_index=True,
    )

    successful_results = [
        result
        for result in results
        if result.get("status") == "success"
    ]

    summary_columns = st.columns(3)
    summary_columns[0].metric(
        "Parser sukses",
        f"{len(successful_results)}/{len(results)}",
    )

    if successful_results:
        fastest_seconds = min(
            float(
                result.get(
                    "performance",
                    {},
                ).get("processing_seconds", 0)
            )
            for result in successful_results
        )
        summary_columns[1].metric(
            "Waktu tercepat",
            f"{fastest_seconds:.3f} detik",
        )
    else:
        summary_columns[1].metric(
            "Waktu tercepat",
            "N/A",
        )

    output_bytes = 0
    if "Output bytes" in metrics_dataframe.columns:
        output_bytes = int(
            metrics_dataframe["Output bytes"].sum()
        )

    summary_columns[2].metric(
        "Total output",
        f"{output_bytes:,} byte",
    )

    docling_result = next(
        (
            result for result in successful_results
            if result.get("parser") == "Docling"
        ),
        None,
    ) or (successful_results[0] if successful_results else None)
    extraction = (
        docling_result.get("json_data", {}).get("information_extraction")
        if docling_result
        else None
    )

    if extraction:
        analyst_analysis = extraction.get("analyst_analysis") or {}
        claim_data = extraction.get("claim_data") or {}
        validation = extraction.get("validation") or {}
        is_langextract = extraction.get("extraction_engine") == "langextract"

        if is_langextract:
            st.subheader("LangExtract: extracted facts dan analyst analysis")
            st.info(
                analyst_analysis.get("case_snapshot")
                or "Fact extraction selesai; belum ada snapshot analyst."
            )
            source_stage = extraction.get("source_stage", "markdown")
            stage_label = (
                "PDF → MinerU pipeline lokal → Markdown → LangExtract"
                if source_stage == "mineru_local_markdown"
                else "PDF → VLM/Docling → Markdown → LangExtract"
            )
            st.caption(
                f"Stage: `{stage_label}` | "
                f"Model: `{extraction.get('model', '-')}` | "
                "Facts dan derived values dipisahkan."
            )
            detection = extraction.get("detection", {})
            if detection.get("profile"):
                st.caption(
                    f"Tipe terdeteksi: **{detection['profile']}** "
                    f"({float(detection.get('confidence', 0)) * 100:.0f}% confidence)"
                )
            insight_columns = st.columns(4)
            insight_columns[0].metric("Halaman", extraction.get("pages_analyzed", 0))
            insight_columns[1].metric("Grounded facts", validation.get("grounded_item_count", 0))
            insight_columns[2].metric("Ungrounded", validation.get("ungrounded_item_count", 0))
            review_count = (
                len(analyst_analysis.get("inconsistencies", []))
                + len(analyst_analysis.get("missing_information", []))
                + len(analyst_analysis.get("potential_risks", []))
            )
            insight_columns[3].metric("Review flags", review_count)

            visit_type = analyst_analysis.get("visit_type") or {}
            if visit_type:
                st.write(
                    f"**Visit type:** `{visit_type.get('value', 'unknown')}` "
                    f"({visit_type.get('status', 'unknown')})"
                )
                if visit_type.get("evidence"):
                    st.caption("Evidence: " + " | ".join(str(item) for item in visit_type["evidence"]))

            summaries = st.expander("Ringkasan analyst (derived)")
            with summaries:
                st.write("**Medical summary:**", analyst_analysis.get("medical_summary", "-"))
                st.write("**Financial summary:**", analyst_analysis.get("financial_summary", "-"))
                st.write("**Claim story:**", analyst_analysis.get("claim_story", "-"))

            icd_suggestions = analyst_analysis.get("icd_suggestions", [])
            if icd_suggestions:
                st.warning("ICD suggestions berikut BUKAN fakta dokumen dan memerlukan validasi coder.")
                st.json(icd_suggestions, expanded=False)
            provenance = st.expander("Intermediate items dan provenance")
            with provenance:
                st.json(extraction.get("provenance", {}), expanded=False)
        else:
            st.subheader("Insight analisis dokumen")
            st.info(extraction.get("summary", "Belum ada summary."))
            insight_columns = st.columns(3)
            insight_columns[0].metric("Halaman dianalisis", extraction.get("pages_analyzed", 0))
            insight_columns[1].metric("Status analisis", "selesai")
            insight_columns[2].metric(
                "Flag risiko",
                len(extraction.get("inconsistencies", []))
                + len(extraction.get("missing_information", [])),
            )

        missing_information = (
            analyst_analysis.get("missing_information", [])
            or extraction.get("missing_information", [])
        )
        inconsistencies = (
            analyst_analysis.get("inconsistencies", [])
            or extraction.get("inconsistencies", [])
        )
        potential_risks = analyst_analysis.get("potential_risks", [])
        review_flags = [*inconsistencies, *missing_information, *potential_risks]
        if review_flags:
            st.markdown("**Perlu review manual**")
            for review_flag in dict.fromkeys(review_flags):
                st.warning(review_flag)

        usage = extraction.get("usage", {})
        cost = extraction.get("cost", {})
        if usage:
            st.subheader("Usage dan estimasi cost")
            _render_usage_metrics(usage, cost)

    (
        raw_ocr_tab,
        markdown_tab,
        json_tab,
        usage_tab,
        similarity_tab,
        log_tab,
        debug_prompt_tab,
        download_tab,
    ) = st.tabs(
        [
            "Raw OCR",
            "Markdown",
            "JSON",
            "Usage & Biaya",
            "Kemiripan",
            "Log & Error",
            "Debug prompt",
            "Download",
        ]
    )

    with raw_ocr_tab:
        raw_ocr_pages = (
            docling_result.get("json_data", {}).get("raw_ocr_pages", [])
            if docling_result
            else []
        )
        if raw_ocr_pages:
            for page in raw_ocr_pages:
                page_number = page.get("page_number", "?")
                with st.expander(f"Halaman {page_number}", expanded=False):
                    st.text_area(
                        f"Raw OCR halaman {page_number}",
                        value=page.get("raw_text") or "(tidak ada raw text)",
                        height=300,
                        label_visibility="collapsed",
                        key=f"raw_ocr_{page_number}",
                    )
                    if page.get("errors"):
                        st.warning("; ".join(map(str, page["errors"])))
        else:
            st.info("Raw OCR native tersedia setelah Information Extraction dijalankan.")

    with markdown_tab:
        markdown_columns = st.columns(len(results))

        for column, result in zip(
            markdown_columns,
            results,
        ):
            with column:
                parser_name = result.get(
                    "parser",
                    "Unknown",
                )
                st.markdown(f"### {parser_name}")

                if result.get("status") == "success":
                    st.text_area(
                        f"Markdown {parser_name}",
                        value=result.get("markdown", ""),
                        height=600,
                        label_visibility="collapsed",
                        key=f"view_{parser_name}",
                    )
                else:
                    st.error(
                        result.get(
                            "error",
                            "Unknown error",
                        )
                    )

    with json_tab:
        if docling_result and docling_result.get("status") == "success":
            grouped = docling_result.get("grouped_claim_form") or (
                docling_result.get("json_data", {}).get("grouped_claim_form")
            )
            if grouped:
                if extraction:
                    grouped = refresh_grouped_decisions(extraction, grouped)
                _render_grouped_claim(grouped)
                st.divider()
            claim_form = docling_result.get("claim_form") or (
                docling_result.get("json_data", {}).get("claim_form")
            )
            if claim_form:
                with st.expander("Claim form lama (kompatibilitas)", expanded=False):
                    _render_claim_form(claim_form)
            if not grouped and not claim_form:
                st.info(
                    "Claim form belum tersedia. Jalankan information extraction "
                    "untuk menghasilkan JSON ringkas."
                )
                st.json(
                    docling_result.get("json_data", {}).get("information_extraction", {}),
                    expanded=False,
                )
        else:
            st.info("Structured extraction belum tersedia.")

        st.divider()
        selected_result_name = st.selectbox(
            "Pilih parser",
            options=[
                result.get("parser", "Unknown")
                for result in results
            ],
        )

        selected_result = next(
            result
            for result in results
            if result.get("parser", "Unknown")
            == selected_result_name
        )

        if selected_result.get("status") == "success":
            st.json(
                selected_result.get("json_data", {}),
                expanded=False,
            )
        else:
            st.error(
                selected_result.get(
                    "error",
                    "Unknown error",
                )
            )

    with usage_tab:
        st.caption(
            "Token per dokumen untuk setiap tahap model remote. Angka "
            "bertanda Estimasi/Campuran berasal dari perkiraan tiktoken, bukan "
            "tagihan aktual."
        )
        usage_rows: list[dict[str, Any]] = []
        document_totals: list[dict[str, Any]] = []
        for result in results:
            parser_name = result.get("parser", "Unknown")
            stage_reports = usage_stage_reports(result)
            for report in stage_reports:
                report_cost = _usage_cost(report)
                usage_rows.append({
                    "Parser": parser_name,
                    "Tahap": USAGE_STAGE_LABELS.get(
                        str(report.get("stage")), str(report.get("stage") or "-")
                    ),
                    "Model": report.get("model") or "-",
                    "Input": int(report.get("input_tokens", 0) or 0),
                    "Output": int(report.get("output_tokens", 0) or 0),
                    "Reasoning": int(report.get("reasoning_tokens", 0) or 0),
                    "Cached": int(report.get("cached_tokens", 0) or 0),
                    "Total": int(report.get("total_tokens", 0) or 0),
                    "Requests": int(report.get("requests", 0) or 0),
                    "Sumber": USAGE_SOURCE_LABELS.get(
                        str(report.get("source", "none")),
                        str(report.get("source", "none")),
                    ),
                    "Cost USD": round(report_cost["usd"], 6),
                    "Cost IDR": round(report_cost["idr"], 2),
                })
            total = combine_usage_reports(stage_reports, stage="document_total")
            total_cost = _usage_cost(total)
            document_totals.append({
                "Parser": parser_name,
                "Input": int(total.get("input_tokens", 0) or 0),
                "Output": int(total.get("output_tokens", 0) or 0),
                "Total": int(total.get("total_tokens", 0) or 0),
                "Requests": int(total.get("requests", 0) or 0),
                "Sumber": USAGE_SOURCE_LABELS.get(
                    str(total.get("source", "none")), str(total.get("source", "none"))
                ),
                "Cost USD": round(total_cost["usd"], 6),
                "Cost IDR": round(total_cost["idr"], 2),
            })

        if usage_rows:
            st.markdown("**Per tahap**")
            st.dataframe(pd.DataFrame(usage_rows), use_container_width=True, hide_index=True)

            st.markdown("**Total per dokumen**")
            totals_frame = pd.DataFrame(document_totals)
            st.dataframe(totals_frame, use_container_width=True, hide_index=True)

            grand = totals_frame[["Input", "Output", "Total", "Requests"]].sum()
            grand_cost_usd = float(totals_frame["Cost USD"].sum())
            grand_cost_idr = float(totals_frame["Cost IDR"].sum())
            st.markdown("**Total seluruh parser**")
            grand_columns = st.columns(5)
            grand_columns[0].metric("Input tokens", f"{int(grand['Input']):,}")
            grand_columns[1].metric("Output tokens", f"{int(grand['Output']):,}")
            grand_columns[2].metric("Total tokens", f"{int(grand['Total']):,}")
            grand_columns[3].metric("Cost USD", f"${grand_cost_usd:.6f}")
            grand_columns[4].metric("Cost IDR", f"Rp {grand_cost_idr:,.2f}")

            with st.expander("Rincian JSON usage"):
                for result in results:
                    usage_payload = result.get("usage") or {}
                    st.json(
                        {
                            "parser": result.get("parser", "Unknown"),
                            "usage": usage_payload,
                        },
                        expanded=False,
                    )
        else:
            st.info("Belum ada data usage untuk ditampilkan.")

        st.caption(
            "Tarif yang dipakai — VLM: "
            f"${LLM_CONFIG.vlm_input_rate_per_million}/1M input, "
            f"${LLM_CONFIG.vlm_output_rate_per_million}/1M output. "
            "Extraction: "
            f"${LLM_CONFIG.langextract_input_rate_per_million}/1M input, "
            f"${LLM_CONFIG.langextract_output_rate_per_million}/1M output. "
            f"Kurs USD→IDR: {LLM_CONFIG.usd_to_idr:,.0f}."
        )

    with similarity_tab:
        st.caption(
            "Kemiripan hanya membandingkan teks Markdown dan bukan "
            "ukuran akurasi absolut."
        )
        st.dataframe(
            similarity_matrix(results),
            use_container_width=True,
        )

    with log_tab:
        for result in results:
            parser_name = result.get("parser", "Unknown")
            parser_status = result.get("status", "unknown")

            with st.expander(
                f"{parser_name} | {parser_status}",
                expanded=parser_status == "error",
            ):
                if result.get("error"):
                    st.error(result["error"])

                st.code(
                    result.get("stdout")
                    or "(stdout kosong)",
                    language="text",
                )

                if result.get("stderr"):
                    st.code(
                        result["stderr"],
                        language="text",
                    )

                metadata = result.get("metadata")
                if metadata:
                    st.markdown("**Metadata parser**")
                    st.json(metadata, expanded=False)

    with debug_prompt_tab:
        debug_prompts = (
            docling_result.get("json_data", {}).get("debug_prompts", {})
            if docling_result
            else {}
        )
        if not debug_prompts:
            st.info("Debug prompt belum tersedia.")
        else:
            st.caption(
                "API key tidak ditampilkan. Metadata OCR lokal/API dan "
                "LangExtract ditampilkan tanpa prompt rahasia."
            )

            ocr_debug = debug_prompts.get("ocr", {})
            st.markdown("**OCR / parser conversion**")
            st.json(ocr_debug, expanded=False)

            extraction_debug = debug_prompts.get("extraction")
            if extraction_debug:
                st.markdown("**Information extraction / LangExtract**")
                st.write(f"**Model:** `{extraction_debug.get('model', '-')}`")
                st.write(
                    "**Halaman gambar:** "
                    + ", ".join(
                        str(page)
                        for page in extraction_debug.get("image_pages", [])
                    )
                    if extraction_debug.get("image_pages")
                    else "**Halaman gambar:** tidak ada"
                )
                st.markdown("**System prompt**")
                st.code(extraction_debug.get("system", ""), language="text")
                st.markdown("**User prompt / text content**")
                st.code(
                    extraction_debug.get("user_text", ""),
                    language="text",
                )
            else:
                st.info(
                    "Information extraction tidak dijalankan, jadi prompt "
                    "extraction tidak tersedia."
                )

            st.markdown("**Business rules (1 pintu: ClaimRulebook)**")
            try:
                from schemas.claim_rulebook import rulebook_prompt_section

                st.code(rulebook_prompt_section(), language="text")
                with st.expander("JSON Schema rulebook (yang dikirim ke LLM)"):
                    from schemas.claim_rulebook import DEFAULT_RULEBOOK

                    st.json(
                        DEFAULT_RULEBOOK.model_json_schema(), expanded=False
                    )
            except Exception as exc:
                st.warning(f"Rulebook tidak dapat dimuat: {exc}")

    with download_tab:
        for download_index, result in enumerate(results):
            parser_name = result.get("parser", "Unknown")
            safe_parser_name = "".join(
                character.lower()
                if character.isalnum()
                else "_"
                for character in parser_name
            ).strip("_")

            st.markdown(f"#### {parser_name}")
            download_columns = st.columns(2)

            download_columns[0].download_button(
                f"Download Markdown {parser_name}",
                data=result.get("markdown", ""),
                file_name=f"{safe_parser_name}_output.md",
                mime="text/markdown",
                disabled=result.get("status") != "success",
                key=f"download_md_{download_index}",
            )

            download_columns[1].download_button(
                f"Download JSON {parser_name}",
                data=json.dumps(
                    result.get("json_data", {}),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                file_name=f"{safe_parser_name}_output.json",
                mime="application/json",
                disabled=result.get("status") != "success",
                key=f"download_json_{download_index}",
            )

        current_job_dir = (
            Path(st.session_state.job_dir)
            if st.session_state.job_dir
            else None
        )

        if current_job_dir and current_job_dir.exists():
            archive_buffer = io.BytesIO()

            with zipfile.ZipFile(
                archive_buffer,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for artifact_path in current_job_dir.rglob("*"):
                    if artifact_path.is_file():
                        archive.write(
                            artifact_path,
                            artifact_path.relative_to(
                                current_job_dir
                            ),
                        )

            st.download_button(
                "Download seluruh artefak ZIP",
                data=archive_buffer.getvalue(),
                file_name="parser_comparison_artifacts.zip",
                mime="application/zip",
            )

        if docling_result and docling_result.get("status") == "success":
            structured_json = docling_result.get("json_data", {})
            st.download_button(
                "Download structured JSON",
                data=json.dumps(
                    structured_json,
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                file_name=f"{str(docling_result.get('parser', 'parser')).lower()}_structured.json",
                mime="application/json",
                key="download_structured_json",
            )

            extraction_info = structured_json.get("information_extraction", {}) or {}
            extraction_data = (
                extraction_info.get("claim_data")
                or extraction_info.get("combined_extraction")
                or extraction_info
            )
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                pd.DataFrame([extraction_data]).to_excel(
                    writer,
                    index=False,
                    sheet_name="extraction",
                )
                pd.DataFrame(
                    structured_json.get("raw_ocr_pages", [])
                ).to_excel(
                    writer,
                    index=False,
                    sheet_name="raw_ocr",
                )
            st.download_button(
                "Download Excel",
                data=excel_buffer.getvalue(),
                file_name=f"{str(docling_result.get('parser', 'parser')).lower()}_structured.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_excel",
            )

    if st.button("🧹 Bersihkan hasil sesi"):
        current_job_dir = (
            Path(st.session_state.job_dir)
            if st.session_state.job_dir
            else None
        )

        if current_job_dir and current_job_dir.exists():
            shutil.rmtree(
                current_job_dir,
                ignore_errors=True,
            )

        st.session_state.results = []
        st.session_state.job_dir = None
        st.session_state.original_filename = None
        st.rerun()
