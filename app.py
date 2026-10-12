"""Streamlit entrypoint with the Pre-VLM cost gate controls."""

from __future__ import annotations

import streamlit as st

from config import AppConfig
from services.pre_vlm_ui import with_ui_overrides

st.set_page_config(
    page_title="Document Parser Comparison Lab",
    page_icon="📄",
    layout="wide",
)

base_config = AppConfig.from_file()

# Render Pre-VLM controls once, before the main application is imported.
ui_config = with_ui_overrides(base_config)

# app_core creates its own AppConfig at import time. Inject the exact config
# selected in the sidebar so parser execution uses the same UI state.
_original_from_file = AppConfig.from_file
_original_set_page_config = st.set_page_config

AppConfig.from_file = classmethod(lambda cls, path="config.properties": ui_config)
st.set_page_config = lambda *args, **kwargs: None
try:
    import app_core  # noqa: F401,E402
finally:
    AppConfig.from_file = _original_from_file
    st.set_page_config = _original_set_page_config
