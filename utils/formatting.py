"""Display formatting helpers for claim values."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def format_amount(value: Any) -> str:
    """Format numeric amounts whether they arrive as numbers or localized text."""
    if value is None or value == "":
        return "0"
    if isinstance(value, bool):
        return str(value)

    text = str(value).strip()
    cleaned = re.sub(r"[^0-9,.+\-]", "", text)
    if not cleaned:
        return text

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        groups = cleaned.split(",")
        if len(groups) > 1 and len(groups[-1]) == 3:
            cleaned = "".join(groups)
        else:
            cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        groups = cleaned.split(".")
        if all(len(group) == 3 for group in groups[1:]):
            cleaned = "".join(groups)
    elif "." in cleaned and len(cleaned.rsplit(".", 1)[1]) == 3:
        cleaned = cleaned.replace(".", "")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return text
    if amount == amount.to_integral_value():
        return f"{amount:,.0f}"
    return f"{amount:,.2f}"


__all__ = ["format_amount"]