"""Streamlit UI helpers for the pre-VLM cost gate.

Kept separate from the mapping logic so the deterministic mapper remains usable
from tests and non-Streamlit callers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from config import AppConfig

try:  # Streamlit is an application dependency, not a mapping dependency.
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None


def with_ui_overrides(config: AppConfig) -> AppConfig:
    """Return a config copy using the current Streamlit pre-VLM controls."""
    if st is None:
        return config

    # Widgets are created only for the Remote VLM path. Their values persist in
    # session state, so the next run can tune the gate before processing again.
    with st.sidebar:
        st.divider()
        st.subheader("Pre-VLM Cost Gate")
        st.caption(
            "RapidOCR → Regex/RapidFuzz → jika confidence cukup, Remote VLM "
            "dilewati. Gate saat ini bersifat per-dokumen."
        )
        enabled = st.toggle(
            "Enable Pre-VLM Mapping",
            value=config.pre_vlm_mapping_enabled,
            key="pre_vlm_enabled_ui",
            help="Aktifkan cheap mapping sebelum Remote VLM.",
        )
        regex_enabled = st.toggle(
            "Regex mapping",
            value=config.pre_vlm_regex_enabled,
            key="pre_vlm_regex_ui",
            disabled=not enabled,
        )
        fuzzy_enabled = st.toggle(
            "RapidFuzz mapping",
            value=config.pre_vlm_fuzzy_enabled,
            key="pre_vlm_fuzzy_ui",
            disabled=not enabled,
        )
        fuzzy_threshold = st.slider(
            "Fuzzy threshold",
            min_value=70.0,
            max_value=100.0,
            value=float(config.pre_vlm_fuzzy_threshold),
            step=1.0,
            key="pre_vlm_threshold_ui",
            disabled=not (enabled and fuzzy_enabled),
            help="Score minimum untuk menerima fuzzy match.",
        )
        fuzzy_margin = st.slider(
            "Fuzzy margin",
            min_value=0.0,
            max_value=20.0,
            value=float(config.pre_vlm_fuzzy_margin),
            step=1.0,
            key="pre_vlm_margin_ui",
            disabled=not (enabled and fuzzy_enabled),
            help="Selisih minimum dari kandidat kedua agar match tidak ambigu.",
        )
        min_matches = st.number_input(
            "Minimum confident matches",
            min_value=1,
            max_value=13,
            value=int(config.pre_vlm_min_matches),
            step=1,
            key="pre_vlm_min_matches_ui",
            disabled=not enabled,
        )
        min_coverage = st.slider(
            "Minimum coverage",
            min_value=0.10,
            max_value=1.00,
            value=float(config.pre_vlm_min_coverage),
            step=0.05,
            key="pre_vlm_coverage_ui",
            disabled=not enabled,
            help="Proporsi field yang harus confident agar VLM dilewati.",
        )
        cost_per_page = st.number_input(
            "Estimasi cost VLM / halaman (IDR)",
            min_value=0.0,
            value=float(config.pre_vlm_cost_per_page_idr),
            step=100.0,
            key="pre_vlm_cost_page_ui",
            disabled=not enabled,
        )

    return replace(
        config,
        pre_vlm_mapping_enabled=bool(enabled),
        pre_vlm_regex_enabled=bool(regex_enabled),
        pre_vlm_fuzzy_enabled=bool(fuzzy_enabled),
        pre_vlm_fuzzy_threshold=float(fuzzy_threshold),
        pre_vlm_fuzzy_margin=float(fuzzy_margin),
        pre_vlm_min_matches=int(min_matches),
        pre_vlm_min_coverage=float(min_coverage),
        pre_vlm_cost_per_page_idr=float(cost_per_page),
    )


def render_result(mapping: dict[str, Any] | None) -> None:
    """Render an audit panel for one pre-VLM decision."""
    if st is None or not mapping:
        return

    savings = mapping.get("cost_optimization") or {}
    matched = int(mapping.get("matched_fields", 0) or 0)
    total = int(mapping.get("total_fields", 0) or 0)
    coverage = float(mapping.get("coverage", 0) or 0) * 100
    selected_engine = mapping.get("selected_engine", "-")
    should_use_vlm = bool(mapping.get("should_use_vlm", True))

    st.markdown("### Pre-VLM Mapping")
    cols = st.columns(5)
    cols[0].metric("Confident fields", f"{matched}/{total}")
    cols[1].metric("Coverage", f"{coverage:.1f}%")
    cols[2].metric("Decision", "SKIP VLM" if not should_use_vlm else "USE VLM")
    cols[3].metric("Engine", str(selected_engine))
    cols[4].metric("VLM skip rate", f"{float(savings.get('vlm_skip_rate', 0) or 0):.1f}%")

    reason = mapping.get("reason") or "-"
    st.caption(
        f"Reason: `{reason}` · Regex: `{mapping.get('regex_enabled', '-')}` · "
        f"RapidFuzz: `{mapping.get('fuzzy_enabled', '-')}`"
    )

    fields = mapping.get("fields") or {}
    rows = []
    for field, item in fields.items():
        rows.append(
            {
                "Field": field,
                "Method": item.get("method", "-"),
                "Confidence": f"{float(item.get('confidence', 0) or 0):.1f}%",
                "Alias": item.get("matched_alias") or "-",
                "Needs VLM": "YES" if item.get("needs_vlm", True) else "NO",
                "Evidence": item.get("evidence") or "-",
            }
        )
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)

    saved = float(savings.get("estimated_cost_saved_idr", 0) or 0)
    skipped = int(savings.get("skipped_vlm_pages", 0) or 0)
    vlm_pages = int(savings.get("vlm_pages", 0) or 0)
    st.caption(
        f"Estimasi: {skipped} halaman VLM dilewati, {vlm_pages} halaman tetap VLM. "
        f"Potensi saving: Rp {saved:,.0f}."
    )
