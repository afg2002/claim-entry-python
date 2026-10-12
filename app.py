"""Streamlit entrypoint with stable Pre-VLM controls.

The main UI lives in ``app_core``.  Streamlit reruns this entrypoint on every
interaction, so ``app_core`` must be reloaded explicitly; a normal ``import``
would execute it only once and can leave the page blank after a widget change.
"""

from __future__ import annotations

import importlib

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
    try:
        import app_core
    except ImportError:
        # Preserve the normal traceback for genuine application import errors.
        raise
    else:
        # Streamlit reruns the script in the same Python process. A plain
        # ``import app_core`` would therefore skip the main UI on reruns.
        importlib.reload(app_core)
finally:
    AppConfig.from_file = _original_from_file_descriptor
    st.set_page_config = _original_set_page_config
