"""Streamlit UI helpers for the pre-VLM cost gate."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from config import AppConfig

try:
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None

_UI_KEYS = (
    "pre_vlm_enabled_ui",
    "pre_vlm_regex_ui",
    "pre_vlm_fuzzy_ui",
    "pre_vlm_threshold_ui",
    "pre_vlm_margin_ui",
    "pre_vlm_min_matches_ui",
    "pre_vlm_coverage_ui",
    "pre_vlm_cost_page_ui",
)


def _has_ui_state() -> bool:
    return st is not None and all(key in st.session_state for key in _UI_KEYS)


def _read_ui_config(config: AppConfig) -> AppConfig:
    return replace(
        config,
        pre_vlm_mapping_enabled=bool(st.session_state["pre_vlm_enabled_ui"]),
        pre_vlm_regex_enabled=bool(st.session_state["pre_vlm_regex_ui"]),
        pre_vlm_fuzzy_enabled=bool(st.session_state["pre_vlm_fuzzy_ui"]),
        pre_vlm_fuzzy_threshold=float(st.session_state["pre_vlm_threshold_ui"]),
        pre_vlm_fuzzy_margin=float(st.session_state["pre_vlm_margin_ui"]),
        pre_vlm_min_matches=int(st.session_state["pre_vlm_min_matches_ui"]),
        pre_vlm_min_coverage=float(st.session_state["pre_vlm_coverage_ui"]),
        pre_vlm_cost_per_page_idr=float(st.session_state["pre_vlm_cost_page_ui"]),
    )


def with_ui_overrides(config: AppConfig) -> AppConfig:
    """Render controls once, then reuse their Streamlit session state."""
    if st is None:
        return config
    if _has_ui_state():
        return _read_ui_config(config)

    with st.sidebar:
        st.divider()
        st.subheader("Pre-VLM Cost Gate")
        st.caption(
            "RapidOCR → Regex/RapidFuzz → jika confidence cukup, Remote VLM dilewati. "
            "Gate saat ini bersifat per-dokumen."
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
            "Fuzzy threshold", 70.0, 100.0,
            value=float(config.pre_vlm_fuzzy_threshold), step=1.0,
            key="pre_vlm_threshold_ui",
            disabled=not (enabled and fuzzy_enabled),
        )
        fuzzy_margin = st.slider(
            "Fuzzy margin", 0.0, 20.0,
            value=float(config.pre_vlm_fuzzy_margin), step=1.0,
            key="pre_vlm_margin_ui",
        )
        min_matches = st.number_input(
            "Minimum confident matches", 1, 13,
            value=int(config.pre_vlm_min_matches), step=1,
            key="pre_vlm_min_matches_ui", disabled=not enabled,
        )
        min_coverage = st.slider(
            "Minimum coverage", 0.10, 1.00,
            value=float(config.pre_vlm_min_coverage), step=0.05,
            key="pre_vlm_coverage_ui", disabled=not enabled,
        )
        cost_per_page = st.number_input(
            "Estimasi cost VLM / halaman (IDR)", 0.0,
            value=float(config.pre_vlm_cost_per_page_idr), step=100.0,
            key="pre_vlm_cost_page_ui", disabled=not enabled,
        )

    return _read_ui_config(config)


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
    st.caption(
        f"Reason: `{mapping.get('reason') or '-'}` · Regex: `{mapping.get('regex_enabled', '-')}` · "
        f"RapidFuzz: `{mapping.get('fuzzy_enabled', '-')}`"
    )
    fields = mapping.get("fields") or {}
    rows = [
        {
            "Field": field,
            "Method": item.get("method", "-"),
            "Confidence": f"{float(item.get('confidence', 0) or 0):.1f}%",
            "Alias": item.get("matched_alias") or "-",
            "Needs VLM": "YES" if item.get("needs_vlm", True) else "NO",
            "Evidence": item.get("evidence") or "-",
        }
        for field, item in fields.items()
    ]
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    saved = float(savings.get("estimated_cost_saved_idr", 0) or 0)
    skipped = int(savings.get("skipped_vlm_pages", 0) or 0)
    vlm_pages = int(savings.get("vlm_pages", 0) or 0)
    st.caption(
        f"Estimasi: {skipped} halaman VLM dilewati, {vlm_pages} halaman tetap VLM. "
        f"Potensi saving: Rp {saved:,.0f}."
    )
