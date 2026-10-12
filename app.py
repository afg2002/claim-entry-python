"""Streamlit entrypoint with stable Pre-VLM controls."""

from __future__ import annotations

import importlib
import sys

import streamlit as st

from config import AppConfig
from services.pre_vlm_ui import with_ui_overrides

st.set_page_config(
    page_title="Document Parser Comparison Lab",
    page_icon="📄",
    layout="wide",
)

# Build the UI config on every Streamlit rerun.
base_config = AppConfig.from_file()
ui_config = with_ui_overrides(base_config)

# app_core reads AppConfig.from_file() at module import time. Inject the
# current sidebar state only for the duration of that import/reload.
_original_from_file_descriptor = AppConfig.__dict__["from_file"]
_original_set_page_config = st.set_page_config

AppConfig.from_file = classmethod(lambda cls, path="config.properties": ui_config)
st.set_page_config = lambda *args, **kwargs: None
try:
    if "app_core" in sys.modules:
        # Streamlit reruns the script in the same Python process. Reload the
        # cached module so the main UI is rebuilt with the latest config.
        importlib.reload(sys.modules["app_core"])
    else:
        # First execution: import exactly once. Do NOT import and reload in
        # the same run, otherwise Streamlit sees duplicate widget elements.
        import app_core  # noqa: F401,E402
finally:
    AppConfig.from_file = _original_from_file_descriptor
    st.set_page_config = _original_set_page_config
