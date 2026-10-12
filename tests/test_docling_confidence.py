from __future__ import annotations

import json
import unittest
import warnings

from docling.datamodel.base_models import ConfidenceReport

from parsers.docling_parser import (
    _attach_confidence_report,
    _serialize_confidence_report,
)


class DoclingConfidenceTests(unittest.TestCase):
    def test_serializes_summary_and_page_scores_as_valid_json(self):
        report = ConfidenceReport(
            parse_score=0.91,
            layout_score=0.82,
            table_score=0.73,
            ocr_score=0.64,
            pages={
                1: {
                    "parse_score": 0.9,
                    "layout_score": 0.8,
                    "table_score": 0.7,
                    "ocr_score": 0.6,
                }
            },
        )

        payload = _serialize_confidence_report(report)

        self.assertIsNotNone(payload)
        self.assertIn("mean_grade", payload)
        self.assertIn("low_grade", payload)
        self.assertEqual(payload["ocr_score"], 0.64)
        self.assertEqual(payload["pages"]["1"]["layout_score"], 0.8)
        self.assertEqual(
            json.loads(json.dumps(payload, allow_nan=False)), payload
        )

    def test_attaches_confidence_report_to_raw_document_payload(self):
        report = {"mean_grade": "GOOD", "ocr_score": 0.95}

        raw = _attach_confidence_report({"texts": []}, report)

        self.assertEqual(raw, {"texts": [], "confidence": report})
        self.assertEqual(_attach_confidence_report({"texts": []}, None), {"texts": []})

    def test_unavailable_scores_serialize_as_null_not_nan(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            report = ConfidenceReport()

        payload = _serialize_confidence_report(report)

        self.assertIsNone(payload["ocr_score"])
        self.assertIsNone(payload["mean_score"])
        json.dumps(payload, allow_nan=False)


if __name__ == "__main__":
    unittest.main()