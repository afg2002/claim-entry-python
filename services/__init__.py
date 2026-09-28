"""Service package with lazy optional integrations.

Importing ``services`` must not require every VLM or LangExtract dependency.
Callers can continue using ``from services import extract_claim_from_markdown``;
the selected function is imported only when requested.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    if name == "extract_with_remote_vlm":
        from .llm_service import extract_with_remote_vlm

        return extract_with_remote_vlm
    if name == "extract_claim_from_markdown":
        from .langextract_service import extract_claim_from_markdown

        return extract_claim_from_markdown
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "extract_with_remote_vlm",
    "extract_claim_from_markdown",
]