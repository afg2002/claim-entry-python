from __future__ import annotations

import unittest

from services.langextract_service import (
    build_claim_contract,
    build_extraction_schema,
)


class LangExtractServiceTests(unittest.TestCase):
    def test_output_schema_is_extraction_envelope(self):
        schema = build_extraction_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("extractions", schema["properties"])

    def test_contract_keeps_explicit_facts_and_groups(self):
        markdown = (
            "Diagnosis: Acute appendicitis\n"
            "ICD-10: K35.80\n"
            "Omeprazole 20 mg twice daily for 5 days\n"
            "Admission Date: 10 September 2026\n"
            "Discharge Date: 14 September 2026\n"
            "Total Amount: Rp 1.250.000\n"
        )

        def make_item(field, value, group_id, *, grounded=True):
            start = markdown.index(value) if grounded else None
            end = start + len(value) if grounded else None
            return {
                "field_name": field,
                "extraction_text": value,
                "source_value": value if grounded else None,
                "source_text": value if grounded else None,
                "model_group_id": group_id,
                "group_id": group_id,
                "group_type": "claim",
                "role": "value",
                "grounding_status": "grounded" if grounded else "ungrounded",
                "char_start": start,
                "char_end": end,
                "source_page": 1,
                "alignment_status": "exact",
                "exact_match": grounded,
                "extraction_id": f"{field}-{group_id}-{value}",
            }

        serialized = {
            "source_characters": len(markdown),
            "items": [
                make_item("diagnosis", "Acute appendicitis", "diagnosis_1"),
                make_item("icd_code", "K35.80", "diagnosis_1"),
                make_item("medication", "Omeprazole", "medication_1"),
                make_item("dosage", "20 mg", "medication_1"),
                make_item("frequency", "twice daily", "medication_1"),
                make_item("duration", "5 days", "medication_1"),
                make_item("admission_date", "10 September 2026", "claim_1"),
                make_item("discharge_date", "14 September 2026", "claim_1"),
                make_item("total_amount", "Rp 1.250.000", "invoice_1"),
                make_item("icd_code", "K35.80", "ungrounded", grounded=False),
            ],
            "invalid_items": [],
            "page_markers": [],
        }
        result = build_claim_contract(serialized)
        claim = result["claim_data"]
        self.assertEqual(claim["diagnosis"], ["Acute appendicitis"])
        self.assertEqual(claim["icd_codes"], ["K35.80"])
        self.assertEqual(len(claim["medications"]), 1)
        self.assertEqual(claim["medications"][0]["dosage"], "20 mg")
        self.assertEqual(claim["medications"][0]["frequency"], "twice daily")
        self.assertEqual(claim["medications"][0]["duration"], "5 days")
        self.assertEqual(claim["diagnosis_records"][0]["document_icd_code"], "K35.80")
        self.assertEqual(result["normalized_claim_data"]["admission_date"], "2026-09-10")
        self.assertEqual(result["normalized_claim_data"]["total_amount"], 1250000)
        self.assertEqual(result["analyst_analysis"]["visit_type"]["status"], "derived")
        self.assertEqual(result["analyst_analysis"]["visit_type"]["value"], "inpatient")
        self.assertNotIn("suggested_icd_code", claim)
        self.assertEqual(result["validation"]["ungrounded_item_count"], 1)

    def test_claim_type_and_visit_type_are_grounded_classifications(self):
        markdown = "Visit Type: Rawat Jalan\nClaim Type: SKD"
        items = []
        for field, value, start_label in (
            ("visit_type", "Rawat Jalan", "Visit Type: "),
            ("claim_type", "SKD", "Claim Type: "),
        ):
            start = markdown.index(start_label) + len(start_label)
            items.append(
                {
                    "field_name": field,
                    "extraction_text": value,
                    "source_value": value,
                    "source_text": value,
                    "model_group_id": "claim_1",
                    "group_id": "claim_1",
                    "group_type": "claim",
                    "role": "value",
                    "grounding_status": "grounded",
                    "char_start": start,
                    "char_end": start + len(value),
                    "source_page": 1,
                    "alignment_status": "exact",
                    "exact_match": True,
                    "extraction_id": field,
                }
            )

        result = build_claim_contract(
            {"items": items, "source_characters": len(markdown)}
        )
        self.assertEqual(result["claim_data"]["visit_type"], "Rawat Jalan")
        self.assertEqual(result["claim_data"]["claim_type"], "SKD")
        self.assertEqual(result["analyst_analysis"]["visit_type"]["value"], "outpatient")
        self.assertEqual(result["analyst_analysis"]["visit_type"]["status"], "extracted")
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["value"], "new")
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["raw_value"], "SKD")
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["status"], "derived")

    def test_visit_and_claim_type_remain_unknown_without_sufficient_evidence(self):
        result = build_claim_contract({"items": [], "source_characters": 12})
        self.assertEqual(result["analyst_analysis"]["visit_type"]["value"], "unknown")
        self.assertEqual(result["analyst_analysis"]["visit_type"]["status"], "unknown")
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["value"], "unknown")
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["status"], "unknown")

    def test_admission_and_discharge_derive_inpatient_only(self):
        markdown = "Admission Date: 10 September 2026\nDischarge Date: 14 September 2026"
        items = []
        for field, value, label in (
            ("admission_date", "10 September 2026", "Admission Date: "),
            ("discharge_date", "14 September 2026", "Discharge Date: "),
        ):
            start = markdown.index(label) + len(label)
            items.append(
                {
                    "field_name": field,
                    "extraction_text": value,
                    "source_value": value,
                    "source_text": value,
                    "model_group_id": "claim_1",
                    "group_id": "claim_1",
                    "group_type": "claim",
                    "role": "value",
                    "grounding_status": "grounded",
                    "char_start": start,
                    "char_end": start + len(value),
                    "source_page": 1,
                    "alignment_status": "exact",
                    "exact_match": True,
                    "extraction_id": field,
                }
            )
        result = build_claim_contract(
            {"items": items, "source_characters": len(markdown)}
        )
        self.assertEqual(result["analyst_analysis"]["visit_type"]["value"], "inpatient")
        self.assertEqual(result["analyst_analysis"]["visit_type"]["status"], "derived")

    def test_review_type_maps_explicit_stage_but_sp4_alone_is_document(self):
        markdown = "Claim Type: Tinjau ulang\nSupporting Document: SP4"
        items = []
        for field, value, label in (
            ("claim_type", "Tinjau ulang", "Claim Type: "),
            ("supporting_document", "SP4", "Supporting Document: "),
        ):
            start = markdown.index(label) + len(label)
            items.append(
                {
                    "field_name": field,
                    "extraction_text": value,
                    "source_value": value,
                    "source_text": value,
                    "model_group_id": "document_1" if field == "supporting_document" else "claim_1",
                    "group_id": "document_1" if field == "supporting_document" else "claim_1",
                    "group_type": "document" if field == "supporting_document" else "claim",
                    "role": "value",
                    "grounding_status": "grounded",
                    "char_start": start,
                    "char_end": start + len(value),
                    "source_page": 1,
                    "alignment_status": "exact",
                    "exact_match": True,
                    "extraction_id": field,
                }
            )
        result = build_claim_contract(
            {"items": items, "source_characters": len(markdown)}
        )
        self.assertEqual(result["analyst_analysis"]["claim_case_type"]["value"], "review")
        self.assertEqual(result["claim_data"]["supporting_documents"], ["SP4"])

    def test_currency_from_amount_is_derived_not_explicit(self):
        markdown = "Total Amount: Rp 986.882"
        item = {
            "field_name": "total_amount",
            "extraction_text": "Rp 986.882",
            "source_value": "Rp 986.882",
            "source_text": "Rp 986.882",
            "model_group_id": "invoice_1",
            "group_id": "invoice_1",
            "group_type": "invoice",
            "role": "value",
            "grounding_status": "grounded",
            "char_start": markdown.index("Rp"),
            "char_end": markdown.index("Rp") + len("Rp 986.882"),
            "source_page": 1,
            "alignment_status": "exact",
            "exact_match": True,
            "extraction_id": "amount-1",
        }
        result = build_claim_contract(
            {"items": [item], "source_characters": len(markdown)}
        )
        self.assertEqual(result["claim_data"]["total_amount"], "Rp 986.882")
        self.assertIsNone(result["claim_data"]["currency"])
        self.assertEqual(result["normalized_claim_data"]["total_amount"], 986882)
        self.assertEqual(result["normalized_claim_data"]["currency"], "IDR")
        self.assertTrue(
            any("Currency IDR" in note for note in result["validation"]["normalization_notes"])
        )


if __name__ == "__main__":
    unittest.main()
