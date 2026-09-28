from __future__ import annotations

import unittest

from services.usage_tracker import (
    ESTIMATED_SOURCE,
    MIXED_SOURCE,
    PROVIDER_SOURCE,
    UNKNOWN_SOURCE,
    UsageLedger,
    build_usage_report,
    combine_usage_reports,
    estimate_text_tokens,
    normalize_usage,
    unavailable_usage_report,
    vlm_usage_from_conversion,
)


class _FakeVlmResponse:
    def __init__(self, total: int) -> None:
        self.usage = {
            "prompt_tokens": total,
            "completion_tokens": total // 2,
            "total_tokens": total,
        }


class NormalizeUsageTests(unittest.TestCase):
    def test_openai_dict_payload(self):
        parsed = normalize_usage(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        self.assertEqual(parsed["input_tokens"], 10)
        self.assertEqual(parsed["output_tokens"], 5)
        self.assertEqual(parsed["total_tokens"], 15)

    def test_gemini_metadata_payload(self):
        parsed = normalize_usage(
            {"prompt_token_count": 7, "candidates_token_count": 3, "total_token_count": 10}
        )
        self.assertEqual(parsed["input_tokens"], 7)
        self.assertEqual(parsed["output_tokens"], 3)
        self.assertEqual(parsed["total_tokens"], 10)

    def test_reasoning_and_cached_details(self):
        parsed = normalize_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "completion_tokens_details": {"reasoning_tokens": 12},
            "prompt_tokens_details": {"cached_tokens": 30},
        })
        self.assertEqual(parsed["reasoning_tokens"], 12)
        self.assertEqual(parsed["cached_tokens"], 30)

    def test_sdk_object_payload(self):
        parsed = normalize_usage(_FakeVlmResponse(20))
        self.assertEqual(parsed["input_tokens"], 20)
        self.assertEqual(parsed["output_tokens"], 10)

    def test_missing_payload_is_none_not_zero(self):
        self.assertIsNone(normalize_usage(None))
        self.assertIsNone(normalize_usage({}))
        self.assertIsNone(normalize_usage({"unrelated": 1}))

    def test_total_is_derived_when_provider_omits_it(self):
        parsed = normalize_usage({"prompt_tokens": 4, "completion_tokens": 6})
        self.assertEqual(parsed["total_tokens"], 10)


class UsageLedgerTests(unittest.TestCase):
    def test_ledger_totals_multiple_pages(self):
        ledger = UsageLedger()
        ledger.record({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
        ledger.record({"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180})
        self.assertTrue(ledger.has_provider_usage)
        report = build_usage_report(ledger, stage="vlm_ocr")
        self.assertEqual(report["input_tokens"], 250)
        self.assertEqual(report["output_tokens"], 50)
        self.assertEqual(report["total_tokens"], 300)
        self.assertEqual(report["requests"], 2)
        self.assertEqual(report["source"], PROVIDER_SOURCE)

    def test_ledger_is_safe_under_concurrent_writers(self):
        import threading

        ledger = UsageLedger()

        def writer() -> None:
            for _ in range(50):
                ledger.record({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        report = build_usage_report(ledger, stage="vlm_ocr")
        self.assertEqual(report["input_tokens"], 200)
        self.assertEqual(report["requests"], 200)

    def test_merge_sums_two_ledgers(self):
        first = UsageLedger()
        first.record({"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
        second = UsageLedger()
        second.record({"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10})
        first.merge(second)
        report = build_usage_report(first, stage="vlm_ocr")
        self.assertEqual(report["input_tokens"], 12)
        self.assertEqual(report["requests"], 2)

    def test_reported_and_estimated_stay_separated(self):
        ledger = UsageLedger()
        ledger.record({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        ledger.estimate(input_tokens=99, output_tokens=99)
        report = build_usage_report(ledger, stage="mixed_stage")
        self.assertEqual(report["source"], MIXED_SOURCE)
        # Provider numbers win for the headline; estimates are kept aside.
        self.assertEqual(report["input_tokens"], 10)
        self.assertEqual(report["output_tokens"], 5)
        self.assertEqual(report["estimated"]["input_tokens"], 99)
        self.assertEqual(report["reported"]["input_tokens"], 10)



class SourceLabelTests(unittest.TestCase):
    def test_estimated_only_stage_is_labelled_estimated(self):
        ledger = UsageLedger()
        ledger.estimate(input_text="halo dunia", output_text="hasil", requests=1)
        report = build_usage_report(ledger, stage="langextract")
        self.assertEqual(report["source"], ESTIMATED_SOURCE)
        self.assertFalse(report["available"])
        self.assertIn("tiktoken", report["estimated"]["method"])

    def test_empty_stage_is_neither_provider_nor_estimate(self):
        report = build_usage_report(UsageLedger(), stage="empty")
        self.assertEqual(report["source"], UNKNOWN_SOURCE)
        self.assertIn("note", report)

    def test_unavailable_report_for_local_only_stage(self):
        report = unavailable_usage_report("vlm_ocr", note="OCR lokal")
        self.assertFalse(report["available"])
        self.assertEqual(report["requests"], 0)
        self.assertEqual(report["note"], "OCR lokal")

    def test_estimate_text_tokens_handles_empty_input(self):
        self.assertEqual(estimate_text_tokens(""), 0)
        self.assertIsNone(estimate_text_tokens(None))

    def test_estimate_text_tokens_counts_real_text(self):
        self.assertGreater(estimate_text_tokens("Halo dunia, claim EXAMPLE-0001"), 0)


class CombineUsageReportsTests(unittest.TestCase):
    def _report(self, stage, input_tokens, output_tokens, cost_usd, *, reported=True):
        return {
            "stage": stage,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "total_tokens": input_tokens + output_tokens,
            "requests": 1,
            "available": reported,
            "source": PROVIDER_SOURCE if reported else ESTIMATED_SOURCE,
            "reported": {
                "input_tokens": input_tokens if reported else None,
                "requests": 1 if reported else 0,
            },
            "estimated": {"input_tokens": None if reported else input_tokens},
            "cost": {"usd": cost_usd, "idr": cost_usd * 18000},
        }

    def test_totals_sum_tokens_and_costs_across_stages(self):
        combined = combine_usage_reports(
            [
                self._report("vlm_ocr", 100, 20, 0.001),
                self._report("extraction", 400, 80, 0.01),
            ],
            stage="document_total",
        )
        self.assertEqual(combined["input_tokens"], 500)
        self.assertEqual(combined["output_tokens"], 100)
        self.assertEqual(combined["total_tokens"], 600)
        self.assertEqual(combined["requests"], 2)
        self.assertAlmostEqual(combined["cost"]["usd"], 0.011)
        self.assertEqual(combined["stages"], ["vlm_ocr", "extraction"])

    def test_missing_reports_are_skipped(self):
        combined = combine_usage_reports(
            [None, self._report("vlm_ocr", 10, 5, 0.001), {}]
        )
        self.assertEqual(combined["input_tokens"], 10)
        self.assertEqual(combined["source"], PROVIDER_SOURCE)

    def test_no_stages_produces_zeroed_report(self):
        combined = combine_usage_reports([], stage="document_total")
        self.assertEqual(combined["total_tokens"], 0)
        self.assertEqual(combined["source"], UNKNOWN_SOURCE)
        self.assertIn("note", combined)



class _FakePrediction:
    def __init__(self, usage, num_tokens=42, generation_time=1.25):
        self.usage = usage
        self.num_tokens = num_tokens
        self.generation_time = generation_time


class _FakePage:
    def __init__(self, page_no, prediction):
        self.page_no = page_no
        self.predictions = type("Predictions", (), {"vlm_response": prediction})()


class _FakeConversion:
    def __init__(self, pages):
        self.pages = pages


class VlmUsageFromConversionTests(unittest.TestCase):
    def test_aggregates_per_page_usage_recorded_by_docling(self):
        converted = _FakeConversion([
            _FakePage(1, _FakePrediction(
                {"prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200}
            )),
            _FakePage(2, _FakePrediction(
                {"prompt_tokens": 700, "completion_tokens": 250, "total_tokens": 950}
            )),
        ])
        report = vlm_usage_from_conversion(
            converted,
            model="test-vlm",
            input_rate_per_million=0.25,
            output_rate_per_million=1.50,
        )
        self.assertEqual(report["input_tokens"], 1600)
        self.assertEqual(report["output_tokens"], 550)
        self.assertEqual(report["total_tokens"], 2150)
        self.assertEqual(report["requests"], 2)
        self.assertTrue(report["available"])
        self.assertEqual(report["source"], PROVIDER_SOURCE)
        self.assertEqual(len(report["per_unit"]), 2)
        self.assertEqual(report["per_unit"][0]["page_no"], 1)
        self.assertEqual(report["per_unit"][0]["num_tokens"], 42)

    def test_falls_back_to_estimates_when_provider_is_silent(self):
        converted = _FakeConversion([_FakePage(1, _FakePrediction(None))])
        report = vlm_usage_from_conversion(
            converted,
            estimated_output_text="# Claim\nTotal Rp 1.250.000",
        )
        self.assertEqual(report["source"], ESTIMATED_SOURCE)
        self.assertFalse(report["available"])
        self.assertGreater(report["output_tokens"], 0)
        self.assertIn("gambar", report["note"])

    def test_pages_without_prediction_are_reported_per_page(self):
        converted = _FakeConversion([_FakePage(1, None)])
        report = vlm_usage_from_conversion(converted)
        self.assertEqual(report["per_unit"][0]["source"], UNKNOWN_SOURCE)
        self.assertEqual(report["requests"], 1)


class ParserWiringTests(unittest.TestCase):
    """The parsers must expose a usage tree the UI and JSON can read."""

    def test_docling_local_ocr_stage_is_not_applicable(self):
        from parsers.docling_parser import _vlm_usage

        config = _config(vlm_input_rate_per_million=0.25)
        report = _vlm_usage(object(), config, "RapidOCR", "# markdown")
        self.assertFalse(report["available"])
        self.assertEqual(report["source"], UNKNOWN_SOURCE)
        self.assertIn("RapidOCR", report["note"])

    def test_docling_remote_vlm_stage_reads_docling_conversion(self):
        from parsers.docling_parser import _vlm_usage

        config = _config(vlm_input_rate_per_million=0.25)
        converted = _FakeConversion([
            _FakePage(1, _FakePrediction(
                {"prompt_tokens": 1000, "completion_tokens": 400, "total_tokens": 1400}
            )),
        ])
        report = _vlm_usage(converted, config, "Remote VLM", "# markdown")
        self.assertTrue(report["available"])
        self.assertEqual(report["input_tokens"], 1000)
        self.assertEqual(report["output_tokens"], 400)
        self.assertEqual(report["stage"], "vlm_ocr")
        self.assertGreater(report["cost"]["usd"], 0)

    def test_mineru_usage_tree_shape(self):
        from services.usage_tracker import combine_usage_reports, unavailable_usage_report

        vlm = unavailable_usage_report("vlm_ocr", note="MinerU lokal")
        extraction = build_usage_report(
            UsageLedger(), stage="langextract", model="m", input_rate_per_million=0.25
        )
        extraction["input_tokens"] = 50
        tree = {
            "vlm_ocr": vlm,
            "extraction": extraction,
            "total": combine_usage_reports([vlm, extraction], stage="document_total"),
        }
        self.assertEqual(sorted(tree), ["extraction", "total", "vlm_ocr"])
        self.assertEqual(tree["total"]["input_tokens"], 50)
        self.assertFalse(tree["vlm_ocr"]["available"])

    def test_result_usage_falls_back_to_json_data_for_old_results(self):
        from comparison.metrics import result_usage, usage_stage_reports

        old_result = {
            "parser": "Docling",
            "json_data": {
                "information_extraction": {
                    "usage": {
                        "input_tokens": 11,
                        "output_tokens": 22,
                        "total_tokens": 33,
                        "requests": 2,
                        "available": True,
                        "source": "provider",
                        "cost": {"usd": 0.5, "idr": 9000},
                    }
                }
            },
        }
        self.assertEqual(result_usage(old_result)["extraction"]["input_tokens"], 11)
        reports = usage_stage_reports(old_result)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["requests"], 2)

    def test_metrics_expose_token_and_cost_columns(self):
        from comparison.metrics import calculate_metrics

        ledger = UsageLedger()
        ledger.record({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})
        result = {
            "parser": "Docling",
            "status": "success",
            "markdown": "# claim",
            "json_data": {},
            "usage": {"vlm_ocr": build_usage_report(ledger, stage="vlm_ocr")},
        }
        metrics = calculate_metrics(result)
        self.assertEqual(metrics["Input tokens"], 100)
        self.assertEqual(metrics["Output tokens"], 20)
        self.assertEqual(metrics["Total tokens"], 120)
        self.assertEqual(metrics["LLM requests"], 1)
        self.assertEqual(metrics["VLM input tokens"], 100)
        self.assertEqual(metrics["Usage source"], "provider")

    def test_config_rates_fall_back_to_generic_rates(self):
        config = _config()
        self.assertEqual(config.rates_for("vlm"), (0.25, 1.50))
        self.assertEqual(config.rates_for("langextract"), (0.25, 1.50))
        self.assertEqual(config.rates_for("unknown"), (0.25, 1.50))

    def test_config_per_stage_rates_are_honoured(self):
        config = _config(
            input_rate_per_million=0.25,
            output_rate_per_million=1.50,
            vlm_input_rate_per_million=0.10,
            vlm_output_rate_per_million=0.40,
        )
        self.assertEqual(config.rates_for("vlm"), (0.10, 0.40))
        self.assertEqual(config.rates_for("langextract"), (0.25, 1.50))

    def test_llm_service_reads_usage_from_dumped_dicts(self):
        from services.llm_service import _usage_value

        # Regression guard: a getattr-only reader returned 0 for every run
        # because the SDK response was already dumped into plain dicts.
        self.assertEqual(_usage_value({"prompt_tokens": 42}, "prompt_tokens"), 42)
        self.assertEqual(
            _usage_value({"completion_tokens_details": {"reasoning_tokens": 7}}, "prompt_tokens"),
            0,
        )
        self.assertEqual(_usage_value(None, "prompt_tokens"), 0)

    def test_openai_client_proxy_records_every_response(self):
        from services.langextract_service import _attach_usage_recorder

        ledger = UsageLedger()
        client = _FakeOpenAIClient()
        model = type("Model", (), {"_client": client})()
        wrapped = _attach_usage_recorder(model, ledger)

        wrapped._client.chat.completions.create(model="m", messages=[])
        wrapped._client.chat.completions.create(model="m", messages=[])
        # An unrelated SDK attribute must still pass through untouched.
        self.assertEqual(wrapped._client.api_key, "fake-key")

        self.assertTrue(ledger.has_provider_usage)
        report = build_usage_report(ledger, stage="langextract")
        self.assertEqual(report["input_tokens"], 200)
        self.assertEqual(report["output_tokens"], 100)
        self.assertEqual(report["requests"], 2)

    def test_usage_recorder_is_optional_when_client_shape_is_unknown(self):
        from services.langextract_service import _attach_usage_recorder

        ledger = UsageLedger()
        model = type("Model", (), {})()
        # A provider with no _client at all must not raise.
        self.assertIs(_attach_usage_recorder(model, ledger), model)
        self.assertFalse(ledger.has_provider_usage)

    def test_gemini_client_proxy_records_usage_metadata(self):
        from services.langextract_service import _attach_usage_recorder

        ledger = UsageLedger()
        model = type("Model", (), {"_client": _FakeGeminiClient()})()
        wrapped = _attach_usage_recorder(model, ledger)
        wrapped._client.models.generate_content(model="g", contents="hi")

        self.assertTrue(ledger.has_provider_usage)
        report = build_usage_report(ledger, stage="langextract")
        self.assertEqual(report["input_tokens"], 11)
        self.assertEqual(report["output_tokens"], 22)


class _FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens


class _FakeOpenAIResponse:
    def __init__(self):
        self.usage = _FakeUsage(100, 50)


class _FakeCompletions:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return _FakeOpenAIResponse()


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    api_key = "fake-key"

    def __init__(self):
        self.chat = _FakeChat()


class _FakeGeminiResponse:
    def __init__(self):
        self.usage_metadata = type(
            "Metadata",
            (),
            {
                "prompt_token_count": 11,
                "candidates_token_count": 22,
                "total_token_count": 33,
            },
        )()


class _FakeModels:
    def generate_content(self, **_kwargs):
        return _FakeGeminiResponse()


class _FakeGeminiClient:
    def __init__(self):
        self.models = _FakeModels()


def _config(**overrides):
    from config import AppConfig

    base = {
        "base_url": "https://gateway.example/openapi",
        "api_key": "k",
        "model": "m",
        "vlm_model": "vlm-m",
        "extraction_model": "ext-m",
        "usd_to_idr": 18000.0,
        "input_rate_per_million": 0.25,
        "output_rate_per_million": 1.50,
    }
    base.update(overrides)
    return AppConfig(**base)


class LangExtractWiringTests(unittest.TestCase):
    """The LangExtract call must use the usage-recording model, not ``config=``."""

    def test_extract_call_uses_prebuilt_model_and_keeps_output_schema(self):
        import inspect

        from services import langextract_service as service

        source = inspect.getsource(service.extract_claim_from_markdown)
        self.assertIn('"model": model', source)
        self.assertIn('"use_schema_constraints": False', source)
        self.assertIn("extract_kwargs[\"output_schema\"] = schema", source)
        # The ledger must be consulted before the report is built, otherwise
        # the reported counters would always be zero.
        self.assertIn("usage_ledger.has_provider_usage", source)

    def test_model_builder_returns_model_and_ledger(self):
        from services.langextract_service import _make_usage_recording_model

        model, ledger = _make_usage_recording_model(
            _config(
                langextract_provider="openai",
                langextract_model="test-model",
                langextract_api_key="fake",
            )
        )
        self.assertFalse(ledger.has_provider_usage)
        self.assertTrue(hasattr(model, "infer"))
        # The recording client must still behave like an SDK client.
        self.assertIsNotNone(getattr(model, "_client", None))

    def test_report_falls_back_to_estimates_when_provider_is_silent(self):
        import services.langextract_service as service

        ledger = UsageLedger()
        report = build_usage_report(
            ledger,
            stage="langextract",
            model="test-model",
            provider="openai",
            usd_to_idr=18000.0,
            input_rate_per_million=0.25,
            output_rate_per_million=1.50,
        )
        self.assertEqual(report["source"], UNKNOWN_SOURCE)
        self.assertTrue(hasattr(service, "_make_usage_recording_model"))


if __name__ == "__main__":
    unittest.main()

