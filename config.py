from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _config_int(values: dict[str, str], key: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(values.get(key, str(default))))
    except (TypeError, ValueError):
        return default


def _config_optional_int(
    values: dict[str, str],
    key: str,
    default: int | None,
    minimum: int = 0,
) -> int | None:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return max(minimum, int(raw_value))
    except (TypeError, ValueError):
        return default


def _config_bool(values: dict[str, str], key: str, default: bool) -> bool:
    raw_value = values.get(key)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _config_float(values: dict[str, str], key: str, default: float) -> float:
    raw_value = values.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AppConfig:
    base_url: str
    api_key: str
    model: str
    vlm_model: str
    extraction_model: str
    usd_to_idr: float
    input_rate_per_million: float
    output_rate_per_million: float

    # Per-stage rates let the VLM/OCR model and the extraction model be billed
    # separately.  They default to the generic rates above so existing
    # config.properties files keep producing the same cost figures.
    vlm_input_rate_per_million: float = 0.0
    vlm_output_rate_per_million: float = 0.0
    langextract_input_rate_per_million: float = 0.0
    langextract_output_rate_per_million: float = 0.0
    # When a provider omits the usage block, fall back to tiktoken estimates
    # instead of showing nothing.  Estimates are always labelled as such.
    usage_estimate_fallback: bool = True

    # LangExtract runs after VLM OCR/Markdown and is intentionally
    # configured separately from the OCR model.
    langextract_provider: str = "openai"
    langextract_model: str = ""
    langextract_base_url: str = ""
    langextract_api_key: str = ""
    langextract_max_char_buffer: int = 2000
    langextract_batch_length: int = 4
    langextract_max_workers: int = 4
    langextract_extraction_passes: int = 1
    langextract_context_window_chars: int | None = None
    langextract_use_output_schema: bool = True
    langextract_enable_icd_suggestions: bool = False
    langextract_icd_suggestion_model: str = ""
    langextract_show_progress: bool = False
    langextract_fallback_to_legacy: bool = False

    @classmethod
    def from_file(cls, path: str | Path = "config.properties") -> "AppConfig":
        config_path = Path(path)
        values: dict[str, str] = {}
        if config_path.exists():
            for raw_line in config_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()

        legacy_model = values.get("MODEL", "")
        default_input_rate = _config_float(values, "INPUT_RATE_PER_MILLION", 0.25)
        default_output_rate = _config_float(values, "OUTPUT_RATE_PER_MILLION", 1.50)
        return cls(
            base_url=values.get("BASE_URL", "").rstrip("/"),
            api_key=values.get("API_KEY", ""),
            model=legacy_model,
            vlm_model=values.get("VLM_MODEL", legacy_model),
            extraction_model=values.get("EXTRACTION_MODEL", legacy_model),
            usd_to_idr=_config_float(values, "USD_TO_IDR", 18000.0),
            input_rate_per_million=default_input_rate,
            output_rate_per_million=default_output_rate,
            vlm_input_rate_per_million=_config_float(
                values, "VLM_INPUT_RATE_PER_MILLION", default_input_rate
            ),
            vlm_output_rate_per_million=_config_float(
                values, "VLM_OUTPUT_RATE_PER_MILLION", default_output_rate
            ),
            langextract_input_rate_per_million=_config_float(
                values, "LANGEXTRACT_INPUT_RATE_PER_MILLION", default_input_rate
            ),
            langextract_output_rate_per_million=_config_float(
                values, "LANGEXTRACT_OUTPUT_RATE_PER_MILLION", default_output_rate
            ),
            usage_estimate_fallback=_config_bool(
                values, "USAGE_ESTIMATE_FALLBACK", True
            ),
            langextract_provider=values.get("LANGEXTRACT_PROVIDER", "openai").lower(),
            langextract_model=values.get("LANGEXTRACT_MODEL", values.get("EXTRACTION_MODEL", legacy_model)),
            langextract_base_url=values.get("LANGEXTRACT_BASE_URL", values.get("BASE_URL", "")).rstrip("/"),
            langextract_api_key=values.get("LANGEXTRACT_API_KEY", values.get("API_KEY", "")),
            langextract_max_char_buffer=_config_int(values, "LANGEXTRACT_MAX_CHAR_BUFFER", 2000, 256),
            langextract_batch_length=_config_int(values, "LANGEXTRACT_BATCH_LENGTH", 4),
            langextract_max_workers=_config_int(values, "LANGEXTRACT_MAX_WORKERS", 4),
            langextract_extraction_passes=_config_int(values, "LANGEXTRACT_EXTRACTION_PASSES", 1),
            langextract_context_window_chars=_config_optional_int(
                values,
                "LANGEXTRACT_CONTEXT_WINDOW_CHARS",
                None,
            ),
            langextract_use_output_schema=_config_bool(
                values,
                "LANGEXTRACT_USE_OUTPUT_SCHEMA",
                True,
            ),
            langextract_enable_icd_suggestions=_config_bool(
                values,
                "LANGEXTRACT_ENABLE_ICD_SUGGESTIONS",
                False,
            ),
            langextract_icd_suggestion_model=values.get(
                "LANGEXTRACT_ICD_SUGGESTION_MODEL",
                values.get("EXTRACTION_MODEL", legacy_model),
            ),
            langextract_show_progress=_config_bool(
                values,
                "LANGEXTRACT_SHOW_PROGRESS",
                False,
            ),
            langextract_fallback_to_legacy=_config_bool(
                values,
                "LANGEXTRACT_FALLBACK_TO_LEGACY",
                False,
            ),
        )

    def validate_for_langextract(self) -> None:
        supported_providers = {"openai", "gemini", "ollama"}
        if self.langextract_provider not in supported_providers:
            raise ValueError(
                "LANGEXTRACT_PROVIDER harus salah satu dari: "
                + ", ".join(sorted(supported_providers))
            )
        if not self.langextract_model:
            raise ValueError("Konfigurasi LangExtract belum lengkap: LANGEXTRACT_MODEL")
        if self.langextract_max_workers < 1:
            raise ValueError("LANGEXTRACT_MAX_WORKERS minimal 1")
        if self.langextract_max_char_buffer < 256:
            raise ValueError("LANGEXTRACT_MAX_CHAR_BUFFER minimal 256")
        if self.langextract_extraction_passes < 1:
            raise ValueError("LANGEXTRACT_EXTRACTION_PASSES minimal 1")
        if self.langextract_batch_length < 1:
            raise ValueError("LANGEXTRACT_BATCH_LENGTH minimal 1")
        if self.langextract_provider in {"openai", "gemini"}:
            resolved_key = (
                self.langextract_api_key
                or os.getenv("LANGEXTRACT_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
            if not resolved_key:
                raise ValueError(
                    "Konfigurasi LangExtract belum lengkap: LANGEXTRACT_API_KEY"
                )

    def validate_for_llm(
        self,
        *,
        require_vlm: bool = False,
        require_extraction: bool = False,
    ) -> None:
        models = {}
        if require_vlm:
            models["VLM_MODEL"] = self.vlm_model
        if require_extraction:
            models["EXTRACTION_MODEL"] = self.extraction_model
        missing = [
            name
            for name, value in {
                "BASE_URL": self.base_url,
                "API_KEY": self.api_key,
                **models,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "Konfigurasi LLM belum lengkap: " + ", ".join(missing)
            )

    def rates_for(self, stage: str) -> tuple[float, float]:
        """Return ``(input_rate, output_rate)`` per million tokens for a stage.

        Unrecognized stages fall back to the generic rates so a new caller
        never silently costs a stage at zero.
        """
        rates = {
            "vlm": (self.vlm_input_rate_per_million, self.vlm_output_rate_per_million),
            "langextract": (
                self.langextract_input_rate_per_million,
                self.langextract_output_rate_per_million,
            ),
        }.get(stage)
        if rates is None:
            return (self.input_rate_per_million, self.output_rate_per_million)
        return tuple(
            value or fallback
            for value, fallback in zip(
                rates,
                (self.input_rate_per_million, self.output_rate_per_million),
            )
        )


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    usd_to_idr: float = 18000,
    input_rate_per_million: float = 0.25,
    output_rate_per_million: float = 1.50,
) -> dict[str, float]:
    input_cost_usd = (input_tokens / 1_000_000) * input_rate_per_million
    output_cost_usd = (output_tokens / 1_000_000) * output_rate_per_million
    total_cost_usd = input_cost_usd + output_cost_usd
    return {
        "usd": total_cost_usd,
        "idr": total_cost_usd * usd_to_idr,
    }
