"""Token usage and cost tracking for every remote model stage.

The pipeline talks to two kinds of remote models:

* the Docling Remote VLM used for OCR/layout/Markdown, and
* the LLM that performs the structured extraction (LangExtract or the legacy
  page-VLM fallback).

Provider coverage is uneven.  Some gateways return a ``usage`` block, some do
not, and LangExtract discards the provider payload entirely before the result
reaches this application.  This module therefore keeps one explicit contract:

* ``reported`` holds numbers that came from the provider verbatim;
* ``estimated`` holds tiktoken approximations for the parts the provider did
  not report;
* ``source`` states which of the two produced the headline numbers.

The two are never merged into an indistinguishable total, so a cost figure is
never silently a guess.  Everything here is dependency-tolerant: an absent
``tiktoken`` or provider payload degrades to a note instead of raising.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

try:  # tiktoken ships with the OpenAI stack but may be pruned in a slim env.
    import tiktoken
except ImportError:  # pragma: no cover - exercised only in a slim environment
    tiktoken = None  # type: ignore[assignment]

ESTIMATE_ENCODING = "cl100k_base"
UNKNOWN_SOURCE = "none"
PROVIDER_SOURCE = "provider"
ESTIMATED_SOURCE = "estimated"
MIXED_SOURCE = "mixed"


def _read(container: Any, key: str) -> Any:
    """Read ``key`` from a mapping or an SDK object without raising."""
    if container is None:
        return None
    if isinstance(container, dict):
        return container.get(key)
    return getattr(container, key, None)


def _first_number(container: Any, *keys: str) -> int:
    for key in keys:
        value = _read(container, key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def normalize_usage(raw: Any) -> dict[str, int] | None:
    """Return canonical counters for a provider usage payload.

    Handles the OpenAI/OpenAI-compatible shape (``prompt_tokens``,
    ``completion_tokens``), the Gemini shape (``prompt_token_count``,
    ``candidates_token_count``) and payloads already nested under ``usage``.
    Returns ``None`` when the payload carries no counter at all, so callers can
    tell "provider said zero" apart from "provider said nothing".
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return _empty_counters(int(raw))

    # Unwrap a raw SDK response that carries the counters under ``usage``.
    # The OpenAI SDK exposes ``response.usage``; a plain dump is already the
    # usage dict itself, in which case ``nested is raw`` guards the recursion.
    nested = _read(raw, "usage")
    if nested is not None and nested is not raw:
        inner = normalize_usage(nested)
        if inner is not None:
            return inner

    input_tokens = _first_number(
        raw, "prompt_tokens", "prompt_token_count", "input_tokens", "promptTokenCount"
    )
    output_tokens = _first_number(
        raw,
        "completion_tokens",
        "candidates_token_count",
        "output_tokens",
        "generated_token_count",
    )
    reasoning_tokens = _first_number(
        _read(raw, "completion_tokens_details"), "reasoning_tokens"
    ) or _first_number(raw, "reasoning_tokens", "thoughts_token_count")
    cached_tokens = _first_number(
        _read(raw, "prompt_tokens_details"), "cached_tokens"
    ) or _first_number(raw, "cached_content_token_count", "cached_tokens")
    total_tokens = _first_number(raw, "total_tokens", "total_token_count")

    if not any((input_tokens, output_tokens, reasoning_tokens, cached_tokens, total_tokens)):
        return None
    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
        "total_tokens": total_tokens,
    }


def _empty_counters(total: int = 0) -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": int(total),
    }


# ---------------------------------------------------------------------------
# Local estimation
# ---------------------------------------------------------------------------

_ENCODING_LOCK = threading.Lock()
_ENCODING: Any = None
_ENCODING_RESOLVED = False


def _encoding() -> Any:
    """Resolve the tiktoken encoding once; ``None`` when unavailable."""
    global _ENCODING, _ENCODING_RESOLVED
    if _ENCODING_RESOLVED:
        return _ENCODING
    with _ENCODING_LOCK:
        if _ENCODING_RESOLVED:
            return _ENCODING
        if tiktoken is not None:
            try:
                _ENCODING = tiktoken.get_encoding(ESTIMATE_ENCODING)
            except Exception:  # pragma: no cover - defensive
                _ENCODING = None
        _ENCODING_RESOLVED = True
        return _ENCODING


def estimation_available() -> bool:
    return _encoding() is not None


def estimate_text_tokens(text: str | None) -> int | None:
    """Approximate the token count of ``text`` with tiktoken.

    Returns ``None`` when tiktoken is unavailable so the caller can degrade to
    "unavailable" instead of inventing a character-based ratio.
    """
    if text is None:
        return None
    if not text:
        return 0
    encoding = _encoding()
    if encoding is None:
        return None
    try:
        return len(encoding.encode(str(text), disallowed_special=()))
    except Exception:  # pragma: no cover - defensive
        return None


def estimate_message_tokens(messages: Iterable[Any]) -> int | None:
    """Approximate prompt tokens for an OpenAI chat ``messages`` list.

    Image parts are not text and are therefore excluded; callers that need
    them must fall back to the provider number for that stage.
    """
    encoding = _encoding()
    if encoding is None:
        return None
    total = 0
    for message in messages or []:
        content = _read(message, "content")
        if isinstance(content, str):
            total += 4
            try:
                total += len(encoding.encode(content, disallowed_special=()))
            except Exception:  # pragma: no cover - defensive
                return None
            continue
        if isinstance(content, Iterable):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                total += 4
                try:
                    text = str(part.get("text") or "")
                    total += len(encoding.encode(text, disallowed_special=()))
                except Exception:  # pragma: no cover - defensive
                    return None
    return total



# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

class UsageLedger:
    """Thread-safe accumulator for one stage of one document run.

    LangExtract and the legacy page-VLM path both fan out over a
    ``ThreadPoolExecutor``, so every mutation happens under a lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reported = {"input": 0, "output": 0, "reasoning": 0, "cached": 0, "total": 0}
        self._estimated = {"input": 0, "output": 0}
        self._requests = 0
        self._reported_requests = 0
        self._raw: list[Any] = []

    def record(self, raw_usage: Any, *, requests: int = 1) -> bool:
        """Record a provider usage payload. Returns ``True`` when parsed."""
        parsed = normalize_usage(raw_usage)
        with self._lock:
            self._requests += max(0, int(requests))
            self._raw.append(raw_usage)
            if parsed is None:
                return False
            self._reported_requests += max(0, int(requests))
            for ledger_key, parsed_key in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
                ("cached", "cached_tokens"),
                ("total", "total_tokens"),
            ):
                self._reported[ledger_key] += parsed[parsed_key]
        return True

    def estimate(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        input_text: str | None = None,
        output_text: str | None = None,
        requests: int = 0,
    ) -> None:
        """Record local tiktoken estimates for the parts the provider skipped."""
        if input_tokens is None and input_text is not None:
            input_tokens = estimate_text_tokens(input_text)
        if output_tokens is None and output_text is not None:
            output_tokens = estimate_text_tokens(output_text)
        with self._lock:
            self._requests += max(0, int(requests))
            if input_tokens:
                self._estimated["input"] += int(input_tokens)
            if output_tokens:
                self._estimated["output"] += int(output_tokens)

    def merge(self, other: "UsageLedger") -> None:
        if other is None:
            return
        with other._lock, self._lock:
            for key in self._reported:
                self._reported[key] += other._reported[key]
            for key in self._estimated:
                self._estimated[key] += other._estimated[key]
            self._requests += other._requests
            self._reported_requests += other._reported_requests
            self._raw.extend(other._raw)

    @property
    def has_provider_usage(self) -> bool:
        with self._lock:
            return self._reported_requests > 0

    @property
    def has_estimates(self) -> bool:
        with self._lock:
            return bool(self._estimated["input"] or self._estimated["output"])

    @property
    def requests(self) -> int:
        with self._lock:
            return self._requests

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "reported": dict(self._reported),
                "estimated": dict(self._estimated),
                "requests": self._requests,
                "reported_requests": self._reported_requests,
                "raw": list(self._raw),
            }


_EMPTY_SNAPSHOT: dict[str, Any] = {
    "reported": {"input": 0, "output": 0, "reasoning": 0, "cached": 0, "total": 0},
    "estimated": {"input": 0, "output": 0},
    "requests": 0,
    "reported_requests": 0,
    "raw": [],
}


def _resolve_source(has_provider: bool, has_estimate: bool) -> str:
    if has_provider and has_estimate:
        return MIXED_SOURCE
    if has_provider:
        return PROVIDER_SOURCE
    if has_estimate:
        return ESTIMATED_SOURCE
    return UNKNOWN_SOURCE



def build_usage_report(
    ledger: UsageLedger | None,
    *,
    stage: str,
    model: str = "",
    provider: str = "",
    usd_to_idr: float = 18000.0,
    input_rate_per_million: float = 0.0,
    output_rate_per_million: float = 0.0,
    note: str | None = None,
    per_unit: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Turn a ledger into the report shape shared by the UI and the JSON files.

    The headline counters keep the historical key names so existing UI code and
    archived JSON stay readable; everything new is additive.
    """
    from config import calculate_cost

    snapshot = ledger.snapshot() if ledger is not None else dict(_EMPTY_SNAPSHOT)
    reported = snapshot["reported"]
    estimated = snapshot["estimated"]

    input_tokens = reported["input"] or estimated["input"]
    output_tokens = reported["output"] or estimated["output"]
    total_tokens = reported["total"] or (input_tokens + output_tokens)

    has_provider = snapshot["reported_requests"] > 0
    has_estimate = bool(estimated["input"] or estimated["output"])
    source = _resolve_source(has_provider, has_estimate)

    report: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reported["reasoning"],
        "cached_tokens": reported["cached"],
        "total_tokens": total_tokens,
        "requests": snapshot["requests"],
        "available": has_provider,
        "source": source,
        "stage": stage,
        "model": model or None,
        "provider": provider or None,
        "reported": {
            "input_tokens": reported["input"] or None,
            "output_tokens": reported["output"] or None,
            "reasoning_tokens": reported["reasoning"] or None,
            "cached_tokens": reported["cached"] or None,
            "total_tokens": reported["total"] or None,
            "requests": snapshot["reported_requests"],
        },
        "estimated": {
            "input_tokens": estimated["input"] or None,
            "output_tokens": estimated["output"] or None,
            "method": f"tiktoken:{ESTIMATE_ENCODING}" if has_estimate else None,
        },
        "cost": {"usd": 0.0, "idr": 0.0},
    }

    report["cost"] = calculate_cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        usd_to_idr=usd_to_idr,
        input_rate_per_million=input_rate_per_million,
        output_rate_per_million=output_rate_per_million,
    )

    notes: list[str] = []
    if note:
        notes.append(note)
    if source == MIXED_SOURCE:
        notes.append(
            "Sebagian token berasal dari respons provider dan sebagian dari "
            "estimasi tiktoken."
        )
    elif source == ESTIMATED_SOURCE:
        notes.append(
            "Provider tidak mengirim blok usage; seluruh angka adalah estimasi "
            "tiktoken, bukan tagihan aktual."
        )
    elif source == UNKNOWN_SOURCE and not note:
        # Only explain an empty stage when the caller gave no reason; an
        # explicit note (for example "local OCR") is already the explanation.
        notes.append(
            "Provider tidak mengirim blok usage dan tidak ada teks yang dapat "
            "diestimasi untuk tahap ini."
        )
    if notes:
        report["note"] = " ".join(notes)
    if per_unit:
        report["per_unit"] = per_unit
    if snapshot["raw"]:
        report["reported_raw"] = snapshot["raw"][:50]

    return report


def unavailable_usage_report(stage: str, note: str, **kwargs: Any) -> dict[str, Any]:
    """Report for a stage that was never executed (for example local OCR)."""
    report = build_usage_report(None, stage=stage, note=note, **kwargs)
    report["available"] = False
    report["source"] = UNKNOWN_SOURCE
    return report



# ---------------------------------------------------------------------------
# Docling Remote VLM helpers
# ---------------------------------------------------------------------------

def usage_from_vlm_prediction(prediction: Any) -> dict[str, Any] | None:
    """Extract counters from one ``VlmPrediction`` produced by Docling."""
    if prediction is None:
        return None
    parsed = normalize_usage(_read(prediction, "usage"))
    num_tokens = _read(prediction, "num_tokens")
    generation_time = _read(prediction, "generation_time")
    if parsed is None and not isinstance(num_tokens, (int, float)):
        return None
    result: dict[str, Any] = {"reported": parsed}
    if isinstance(num_tokens, (int, float)) and not isinstance(num_tokens, bool):
        result["num_tokens"] = int(num_tokens)
    if isinstance(generation_time, (int, float)) and not isinstance(generation_time, bool):
        result["generation_time"] = round(float(generation_time), 3)
    return result


def vlm_usage_from_conversion(
    converted: Any,
    *,
    stage: str = "vlm_ocr",
    model: str = "",
    usd_to_idr: float = 18000.0,
    input_rate_per_million: float = 0.0,
    output_rate_per_million: float = 0.0,
    estimated_output_text: str = "",
    estimated_input_text: str = "",
) -> dict[str, Any]:
    """Aggregate the Remote VLM usage Docling already recorded per page.

    Docling stores the gateway ``usage`` payload on
    ``ConversionResult.pages[*].predictions.vlm_response.usage``.  The pipeline
    resource-release step clears parsed pages and image caches but not
    predictions, so the counters survive ``DocumentConverter.convert``.
    """
    ledger = UsageLedger()
    per_page: list[dict[str, Any]] = []
    pages = _read(converted, "pages") or []

    for page in pages:
        predictions = _read(page, "predictions")
        prediction = _read(predictions, "vlm_response")
        page_no = _read(page, "page_no")
        if prediction is None:
            # Docling attempted the page but produced no prediction; it still
            # consumed one model call, so it is counted as a request.
            ledger.record(None)
            per_page.append({"page_no": page_no, "source": UNKNOWN_SOURCE})
            continue
        ledger.record(_read(prediction, "usage"))
        details = usage_from_vlm_prediction(prediction) or {}
        per_page.append({
            "page_no": page_no,
            "source": PROVIDER_SOURCE if details.get("reported") else UNKNOWN_SOURCE,
            "usage": details.get("reported"),
            "num_tokens": details.get("num_tokens"),
            "generation_time": details.get("generation_time"),
        })

    note = None
    if not ledger.has_provider_usage and (estimated_input_text or estimated_output_text):
        ledger.estimate(
            input_text=estimated_input_text or None,
            output_text=estimated_output_text or None,
            requests=len(pages) or 1,
        )
        # Page images cannot be tokenized locally, so only the text side is
        # estimated and the note says so explicitly.
        note = (
            "Halaman VLM berupa gambar sehingga sisi input tidak dapat "
            "ditokenisasi lokal; hanya output Markdown yang diestimasi."
        )

    return build_usage_report(
        ledger,
        stage=stage,
        model=model,
        provider="remote_vlm",
        usd_to_idr=usd_to_idr,
        input_rate_per_million=input_rate_per_million,
        output_rate_per_million=output_rate_per_million,
        note=note,
        per_unit=per_page or None,
    )


__all__ = [
    "ESTIMATED_SOURCE",
    "MIXED_SOURCE",
    "PROVIDER_SOURCE",
    "UNKNOWN_SOURCE",
    "UsageLedger",
    "build_usage_report",
    "combine_usage_reports",
    "estimate_message_tokens",
    "estimate_text_tokens",
    "estimation_available",
    "normalize_usage",
    "unavailable_usage_report",
    "usage_from_vlm_prediction",
    "vlm_usage_from_conversion",
]



def combine_usage_reports(
    reports: Iterable[dict[str, Any] | None],
    *,
    stage: str = "total",
) -> dict[str, Any]:
    """Sum several per-stage reports into one document-level report.

    Cost is summed per stage rather than recomputed from merged tokens, because
    the VLM and the extraction model can carry different rates.
    """
    totals: dict[str, int] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": 0,
        "requests": 0,
    }
    reported = {"input": 0, "output": 0, "reasoning": 0, "cached": 0, "total": 0, "requests": 0}
    estimated = {"input": 0, "output": 0}
    usd = 0.0
    idr = 0.0
    stages: list[str] = []
    available = False
    has_estimate = False

    for report in reports or []:
        if not isinstance(report, dict):
            continue
        stages.append(str(report.get("stage") or "unknown"))
        available = available or bool(report.get("available"))
        totals["input_tokens"] += int(report.get("input_tokens") or 0)
        totals["output_tokens"] += int(report.get("output_tokens") or 0)
        totals["reasoning_tokens"] += int(report.get("reasoning_tokens") or 0)
        totals["cached_tokens"] += int(report.get("cached_tokens") or 0)
        totals["total_tokens"] += int(report.get("total_tokens") or 0)
        totals["requests"] += int(report.get("requests") or 0)

        stage_reported = report.get("reported") or {}
        for key in ("input", "output", "reasoning", "cached", "total"):
            reported[key] += int(stage_reported.get(f"{key}_tokens") or 0)
        reported["requests"] += int(stage_reported.get("requests") or 0)

        stage_estimated = report.get("estimated") or {}
        estimated["input"] += int(stage_estimated.get("input_tokens") or 0)
        estimated["output"] += int(stage_estimated.get("output_tokens") or 0)
        has_estimate = has_estimate or bool(
            stage_estimated.get("input_tokens") or stage_estimated.get("output_tokens")
        )

        cost = report.get("cost") or {}
        usd += float(cost.get("usd") or 0.0)
        idr += float(cost.get("idr") or 0.0)

    if not totals["total_tokens"]:
        totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]

    combined: dict[str, Any] = {
        **totals,
        "available": available,
        "source": _resolve_source(available, has_estimate),
        "stage": stage,
        "stages": stages,
        "model": None,
        "provider": None,
        "reported": {
            "input_tokens": reported["input"] or None,
            "output_tokens": reported["output"] or None,
            "reasoning_tokens": reported["reasoning"] or None,
            "cached_tokens": reported["cached"] or None,
            "total_tokens": reported["total"] or None,
            "requests": reported["requests"],
        },
        "estimated": {
            "input_tokens": estimated["input"] or None,
            "output_tokens": estimated["output"] or None,
            "method": f"tiktoken:{ESTIMATE_ENCODING}" if has_estimate else None,
        },
        "cost": {"usd": usd, "idr": idr},
    }
    if not available and not has_estimate:
        combined["note"] = "Tidak ada tahap model remote yang dijalankan."
    return combined

