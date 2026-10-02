"""Tests for the compact claim-form projection and its business rules.

Everything here is offline: no network call, no LangExtract run. The tests
feed hand-built contract payloads so the shape and the business decisions can
be verified without a provider.
"""

from __future__ import annotations

import json
import unittest

from services.claim_form import (
    CLAIM_FORM_VERSION,
    DEFAULT_CURRENCY,
    DEFAULT_EXCHANGE_RATE,
    build_claim_form,
    build_full_claim_json,
    build_grouped_claim_form,
    refresh_grouped_decisions,
)
from services.claim_rules import (
    canonical_claim_case_type,
    canonical_visit_type,
    document_title,
    visit_type_from_documents,
)
from services.langextract_service import build_claim_contract


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


class _FakeClient:
    """Minimal OpenAI-shaped client that replays a canned response."""

    def __init__(self, payload):
        self._payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.chat = _FakeChat(self._payload)


class _FakeChat:
    def __init__(self, payload):
        self.completions = _FakeCompletions(payload)


class _FakeCompletions:
    def __init__(self, payload):
        self._payload = payload

    def create(self, **_kwargs):
        return _FakeResponse(self._payload)


class _FakeResponse:
    def __init__(self, payload):
        self.choices = [_FakeChoice(payload)]


class _FakeChoice:
    def __init__(self, payload):
        self.message = _FakeMessage(payload)


class _FakeMessage:
    def __init__(self, payload):
        self.content = payload


def _item(field, value, *, group="g1", family="claim", start=0, page=1):
    """Build one grounded intermediate item.

    ``grounding_status`` is set explicitly because the contract builder reads it
    straight off the item; a real run gets it from the LangExtract alignment
    step in ``_item_from_extraction``.
    """
    return {
        "field_name": field,
        "extraction_text": value,
        "source_value": value,
        "source_text": value,
        "model_group_id": group,
        "group_id": group,
        "group_type": family,
        "role": "value",
        "fact_status": "explicit",
        "char_start": start,
        "char_end": start + len(value),
        "source_page": page,
        "alignment_status": "exact",
        "grounding_status": "grounded",
        "exact_match": True,
        "extraction_id": f"{field}-{group}",
    }


def _contract(items):
    return build_claim_contract(
        {"source_characters": 500, "items": items, "page_markers": []}
    )


IDENTITY_ITEMS = [
    _item("patient_name", "ANDRIAN YUSUP", group="i1", family="identity", start=0),
    _item("date_of_birth", "1955-12-19", group="i2", family="identity", start=20),
    _item("patient_id", "51197086", group="i3", family="identity", start=40),
    _item("mobile_phone", "6283830303987", group="i4", family="identity", start=60),
    _item("policy_holder_name", "ANDRIAN YUSUP", group="i5", family="identity", start=80),
    _item("policy_number", "POL-998", group="i6", family="identity", start=100),
]

CLAIM_ITEMS = [
    _item("provider_name", "MITRA KELUARGA PONDOK TJANDRA", group="p1", family="provider", start=120),
    _item("provider_code", "HP000000GEN14889", group="p2", family="provider", start=150),
    _item("doctor_name", "Dr. Andrianto, Sp.OT", group="p3", family="provider", start=170),
    _item("claim_type", "REIMBURSEMENT", group="c1", start=200),
    _item("account_no", "123", group="c2", start=220),
    _item("received_date", "2026-09-11", group="c3", start=240),
    _item("hospitalization_date", "2026-08-31", group="c4", start=260),
    _item("total_amount", "1.215.882", group="c5", family="invoice", start=280),
    _item("diagnosis", "Dislocation sprain", group="d1", family="diagnosis", start=300),
]

PAGES_FORM = [
    {"page_number": 1, "raw_text": "FORMULIR PENGAJUAN KLAIM\nANDRIAN YUSUP"},
    {"page_number": 2, "raw_text": "KWITANSI RAWAT JALAN\nTotal Rp 1.215.882"},
]
PAGES_RESUME = [
    {"page_number": 1, "raw_text": "FORMULIR PENGAJUAN KLAIM\nANDRIAN YUSUP"},
    {"page_number": 2, "raw_text": "RESUME MEDIS\nRawat inap 3 hari"},
]


class ClaimFormShapeTests(unittest.TestCase):
    def test_form_exposes_the_claim_form_blocks(self):
        form = build_claim_form(_contract(IDENTITY_ITEMS + CLAIM_ITEMS))
        self.assertEqual(form["schema_version"], CLAIM_FORM_VERSION)
        for block in (
            "document", "member_info", "policy_info", "claim", "visit",
            "provider", "medical", "financial", "service_lines",
            "medications", "procedures", "lab_results", "review",
        ):
            self.assertIn(block, form)

    def test_member_info_uses_form_field_names(self):
        form = build_claim_form(_contract(IDENTITY_ITEMS + CLAIM_ITEMS))
        member = form["member_info"]
        self.assertEqual(member["insured_name"], "ANDRIAN YUSUP")
        self.assertEqual(member["dob"], "1955-12-19")
        self.assertEqual(member["patient_id"], "51197086")
        self.assertEqual(member["mobile_phone"], "6283830303987")
        self.assertNotIn("date_of_birth", member)
        self.assertNotIn("patient_name", member)

    def test_policy_block_is_separate_from_member(self):
        form = build_claim_form(_contract(IDENTITY_ITEMS + CLAIM_ITEMS))
        self.assertEqual(form["policy_info"]["policy_number"], "POL-998")
        self.assertEqual(form["policy_info"]["policy_holder_name"], "ANDRIAN YUSUP")

    def test_claim_block_carries_account_and_received_date(self):
        form = build_claim_form(_contract(IDENTITY_ITEMS + CLAIM_ITEMS))
        claim = form["claim"]
        self.assertEqual(claim["account_no"], "123")
        self.assertEqual(claim["received_date"], "2026-09-11")
        self.assertEqual(claim["claim_type_label"], "REIMBURSEMENT")
        self.assertEqual(claim["claim_case_type"], "new")

    def test_form_is_much_smaller_than_the_full_contract(self):
        import json

        contract = _contract(IDENTITY_ITEMS + CLAIM_ITEMS)
        form = build_claim_form(contract, pages=PAGES_FORM)
        self.assertLess(
            len(json.dumps(form, default=str)),
            len(json.dumps(contract, default=str)),
        )

    def test_absent_values_stay_null_for_a_stable_shape(self):
        form = build_claim_form(_contract([]))
        self.assertIsNone(form["member_info"]["insured_name"])
        self.assertIsNone(form["financial"]["amount_incurred"])
        self.assertEqual(form["medications"], [])


class FinancialDefaultTests(unittest.TestCase):
    def test_currency_defaults_to_idr_and_is_marked_as_default(self):
        form = build_claim_form(_contract(CLAIM_ITEMS))
        financial = form["financial"]
        self.assertEqual(financial["currency"], DEFAULT_CURRENCY)
        self.assertEqual(financial["exchange_rate"], DEFAULT_EXCHANGE_RATE)
        self.assertEqual(financial["currency_status"], "default")
        self.assertEqual(financial["exchange_rate_status"], "default")

    def test_explicit_currency_is_kept_and_marked_extracted(self):
        items = CLAIM_ITEMS + [
            _item("currency", "USD", group="f1", family="invoice", start=340)
        ]
        form = build_claim_form(_contract(items))
        self.assertEqual(form["financial"]["currency"], "USD")
        self.assertEqual(form["financial"]["currency_status"], "extracted")

    def test_amount_incurred_is_numeric(self):
        form = build_claim_form(_contract(CLAIM_ITEMS))
        self.assertEqual(form["financial"]["amount_incurred"], 1215882)
        self.assertEqual(form["financial"]["total_amount"], 1215882)

    def test_service_line_reports_the_incurred_amount(self):
        form = build_claim_form(_contract(CLAIM_ITEMS))
        self.assertEqual(len(form["service_lines"]), 1)
        self.assertEqual(form["service_lines"][0]["incurred_amount"], 1215882)



class VisitTypeTests(unittest.TestCase):
    def test_receipt_only_bundle_is_outpatient(self):
        form = build_claim_form(
            _contract(CLAIM_ITEMS), pages=PAGES_FORM, classifications=CLASSIFICATIONS_FORM
        )
        visit = form["visit"]
        self.assertEqual(visit["type_of_visit"], "outpatient")
        self.assertEqual(visit["method"], "outpatient_receipt_evidence")
        self.assertEqual(visit["status"], "derived")
        self.assertEqual(visit["hospitalization_date"], "2026-08-31")
        self.assertEqual(visit["no_of_days"], 0)

    def test_resume_bundle_is_inpatient(self):
        form = build_claim_form(
            _contract(CLAIM_ITEMS), pages=PAGES_RESUME, classifications=CLASSIFICATIONS_RESUME
        )
        visit = form["visit"]
        self.assertEqual(visit["type_of_visit"], "inpatient")
        self.assertEqual(visit["method"], "inpatient_document_type")

    def test_explicit_visit_label_wins_over_document_evidence(self):
        items = CLAIM_ITEMS + [
            _item("visit_type", "Rawat Inap", group="v1", start=360)
        ]
        form = build_claim_form(_contract(items), pages=PAGES_FORM)
        self.assertEqual(form["visit"]["type_of_visit"], "inpatient")
        self.assertEqual(form["visit"]["status"], "extracted")

    def test_admission_discharge_pair_is_inpatient(self):
        items = CLAIM_ITEMS + [
            _item("admission_date", "2026-08-30", group="a1", start=380),
            _item("discharge_date", "2026-09-02", group="a2", start=400),
        ]
        form = build_claim_form(_contract(items), pages=PAGES_FORM)
        visit = form["visit"]
        self.assertEqual(visit["type_of_visit"], "inpatient")
        self.assertEqual(visit["admission_date"], "2026-08-30")
        self.assertEqual(visit["discharge_date"], "2026-09-02")
        self.assertEqual(visit["no_of_days"], 3)

    def test_mixed_document_bundle_stays_unknown(self):
        pages = [
            {"page_number": 1, "raw_text": "RESUME MEDIS\nRawat inap"},
            {"page_number": 2, "raw_text": "KWITANSI\nTotal 100"},
        ]
        form = build_claim_form(_contract(CLAIM_ITEMS), pages=pages)
        self.assertEqual(form["visit"]["type_of_visit"], "unknown")
        self.assertIn("note", form["visit"])

    def test_unknown_visit_is_flagged_for_review(self):
        form = build_claim_form(_contract(CLAIM_ITEMS), pages=[])
        self.assertEqual(form["visit"]["type_of_visit"], "unknown")
        self.assertTrue(
            any("Tipe kunjungan" in item for item in form["review"]["needs_attention"])
        )


class ReasonOfVisitTests(unittest.TestCase):
    def test_explicit_reason_is_reported_as_extracted(self):
        items = CLAIM_ITEMS + [
            _item("reason_of_visit", "DISEASE", group="r1", start=420)
        ]
        form = build_claim_form(_contract(items), pages=PAGES_FORM)
        self.assertEqual(form["medical"]["reason_of_visit"], "DISEASE")
        self.assertEqual(form["medical"]["reason_status"], "extracted")

    def test_inpatient_reason_tells_a_story_from_grounded_facts(self):
        items = CLAIM_ITEMS + [
            _item("visit_type", "Rawat Inap", group="v1", start=360),
            _item("admission_date", "2026-08-30", group="a1", start=380),
            _item("discharge_date", "2026-09-02", group="a2", start=400),
        ]
        form = build_claim_form(_contract(items), pages=PAGES_RESUME)
        reason = form["medical"]["reason_of_visit"]
        self.assertEqual(form["medical"]["reason_status"], "derived")
        self.assertIn("Rawat inap", reason)
        self.assertIn("Dislocation sprain", reason)
        self.assertIn("MITRA KELUARGA", reason)
        self.assertIn("2026-08-30", reason)

    def test_outpatient_reason_is_shorter(self):
        form = build_claim_form(_contract(CLAIM_ITEMS), pages=PAGES_FORM)
        reason = form["medical"]["reason_of_visit"]
        self.assertIn("Rawat jalan", reason)
        self.assertIn("2026-08-31", reason)

    def test_reason_is_unknown_when_no_clinical_fact_exists(self):
        items = [_item("policy_number", "POL-1", group="z1", family="identity", start=0)]
        form = build_claim_form(_contract(items), pages=[])
        self.assertIsNone(form["medical"]["reason_of_visit"])
        self.assertEqual(form["medical"]["reason_status"], "unknown")
        self.assertIn("reason_note", form["medical"])



CLASSIFICATIONS_FORM = [
    {"page": 1, "document_type": "Formulir Pengajuan Klaim", "category": "formulir_klaim", "label": "FORMULIR PENGAJUAN KLAIM", "source": "model"},
    {"page": 2, "document_type": "Kwitansi Rawat Jalan", "category": "invoice", "label": "KWITANSI RAWAT JALAN", "source": "model"},
]
CLASSIFICATIONS_RESUME = [
    {"page": 1, "document_type": "Formulir Pengajuan Klaim", "category": "formulir_klaim", "label": "FORMULIR PENGAJUAN KLAIM", "source": "model"},
    {"page": 2, "document_type": "Resume Medis", "category": "resume", "label": "RESUME MEDIS", "source": "model"},
]


class DocumentInventoryTests(unittest.TestCase):
    def test_pages_keep_the_model_own_name_and_business_category(self):
        form = build_claim_form(
            _contract(CLAIM_ITEMS),
            pages=PAGES_FORM,
            classifications=CLASSIFICATIONS_FORM,
        )
        document = form["document"]
        self.assertEqual(document["total_pages"], 2)
        # The name is the model's own wording, not a bucket key.
        self.assertEqual(document["pages"][0]["document_type"], "Formulir Pengajuan Klaim")
        self.assertEqual(document["pages"][0]["label"], "FORMULIR PENGAJUAN KLAIM")
        self.assertEqual(document["pages"][0]["category"], "formulir_klaim")
        self.assertEqual(document["pages"][1]["document_type"], "Kwitansi Rawat Jalan")
        self.assertEqual(document["pages"][1]["source"], "model")

    def test_document_type_is_never_rewritten_into_a_catch_all(self):
        form = build_claim_form(
            _contract(CLAIM_ITEMS), pages=PAGES_FORM, classifications=CLASSIFICATIONS_FORM
        )
        for page in form["document"]["pages"]:
            self.assertTrue(str(page["document_type"]).strip())
            self.assertNotIn(
                str(page["document_type"]).lower(),
                {"other", "unknown", "unclassified", "document", "dokumen"},
            )

    def test_page_title_is_used_when_no_classification_ran(self):
        form = build_claim_form(_contract(CLAIM_ITEMS), pages=PAGES_FORM)
        document = form["document"]
        self.assertEqual(document["pages"][0]["document_type"], "FORMULIR PENGAJUAN KLAIM")
        self.assertEqual(document["pages"][0]["source"], "page_title")
        self.assertIsNone(document["pages"][0]["category"])

    def test_classifier_keeps_whichever_names_the_model_gave(self):
        from services.page_classifier import classify_pages

        pages = [
            {"page_number": 1, "raw_text": "FORMULIR PENGAJUAN KLAIM"},
            {"page_number": 2, "raw_text": "KWITANSI"},
            {"page_number": 3, "raw_text": "RESUME MEDIS"},
        ]
        payload = {
            "pages": [
                {"page": "1", "document_type": "Formulir Pengajuan Klaim",
                 "category": "formulir_klaim", "title": "FORMULIR"},
                {"page": "2", "document_type": "Kwitansi Rawat Jalan",
                 "category": "invoice", "title": "KWITANSI"},
                {"page": "3", "document_type": "Resume Medis",
                 "category": "resume", "title": "RESUME"},
            ]
        }
        result = classify_pages(pages, _config(), client=_FakeClient(payload))
        self.assertEqual(len(result), 3)
        self.assertEqual(
            [item["document_type"] for item in result],
            ["Formulir Pengajuan Klaim", "Kwitansi Rawat Jalan", "Resume Medis"],
        )
        self.assertEqual(
            [item["category"] for item in result],
            ["formulir_klaim", "invoice", "resume"],
        )

    def test_unusual_document_name_is_kept_verbatim(self):
        from services.page_classifier import classify_pages

        pages = [{"page_number": 1, "raw_text": "Surat Pernyataan Tidak Mampu"}]
        payload = {
            "pages": [
                {"page": "1", "document_type": "Surat Pernyataan Tidak Mampu",
                 "category": None, "title": "SURAT PERNYATAAN"}
            ]
        }
        result = classify_pages(pages, _config(), client=_FakeClient(payload))
        self.assertEqual(result[0]["document_type"], "Surat Pernyataan Tidak Mampu")
        self.assertIsNone(result[0]["category"])

    def test_partial_answers_leave_the_other_pages_unnamed(self):
        from services.page_classifier import classify_pages

        pages = [
            {"page_number": 1, "raw_text": "KWITANSI"},
            {"page_number": 2, "raw_text": "RESUME MEDIS"},
        ]
        payload = {
            "pages": [
                {"page": "1", "document_type": "Kwitansi", "category": "invoice", "title": None}
            ]
        }
        result = classify_pages(pages, _config(), client=_FakeClient(payload))
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["document_type"], "Kwitansi")
        # The page the model skipped is left blank rather than invented.
        self.assertIsNone(result[1]["document_type"])
        self.assertIsNone(result[1]["category"])

    def test_non_answer_wording_is_treated_as_no_answer(self):
        from services.page_classifier import classify_pages

        pages = [{"page_number": 1, "raw_text": "x"}]
        payload = {
            "pages": [{"page": "1", "document_type": "other", "category": None, "title": None}]
        }
        result = classify_pages(pages, _config(), client=_FakeClient(payload))
        self.assertIsNone(result[0]["document_type"])

    def test_classifier_does_not_accept_a_category_outside_the_contract(self):
        from services.page_classifier import classify_pages

        pages = [{"page_number": 1, "raw_text": "KWITANSI"}]
        # The response model declares category as a Literal, so a value outside it
        # fails validation. The run must survive that, and must not pass the
        # invented value on as if it were one of our categories.
        payload = {
            "pages": [
                {"page": "1", "document_type": "Kwitansi",
                 "category": "not_a_category", "title": None}
            ]
        }
        result = classify_pages(pages, _config(), client=_FakeClient(payload))
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["category"])
        self.assertEqual(result[0]["source"], "not_named")
        self.assertIn("classification_note", result[0])

    def test_classifier_survives_an_invalid_json_response(self):
        from services.page_classifier import classify_pages

        pages = [{"page_number": 1, "raw_text": "KWITANSI"}]
        result = classify_pages(pages, _config(), client=_FakeClient("{not json"))
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["document_type"])
        self.assertIsNone(result[0]["category"])
        self.assertEqual(result[0]["source"], "not_named")
        self.assertIn("classification_note", result[0])

    def test_document_title_strips_decoration(self):
        self.assertEqual(document_title("  \n KWITANSI\nx"), "KWITANSI")
        self.assertIsNone(document_title("   \n  "))

    def test_visit_type_uses_classified_document_types(self):
        form = build_claim_form(
            _contract(CLAIM_ITEMS),
            pages=PAGES_RESUME,
            classifications=CLASSIFICATIONS_RESUME,
        )
        self.assertEqual(form["visit"]["type_of_visit"], "inpatient")
        self.assertEqual(form["visit"]["method"], "inpatient_document_type")

    def test_visit_type_from_documents_reports_its_method(self):
        value, method, evidence = visit_type_from_documents(["invoice"])
        self.assertEqual(value, "outpatient")
        self.assertEqual(method, "outpatient_receipt_evidence")
        self.assertTrue(evidence)

    def test_visit_type_from_documents_is_unknown_without_evidence(self):
        value, method, _ = visit_type_from_documents(["formulir_klaim"])
        self.assertIsNone(value)
        self.assertEqual(method, "insufficient_document_evidence")


class IcdRecommendationTests(unittest.TestCase):
    def test_missing_icd_with_diagnosis_is_flagged(self):
        form = build_claim_form(_contract(CLAIM_ITEMS), pages=PAGES_FORM)
        self.assertEqual(form["medical"]["icd_codes"], [])
        self.assertTrue(
            any("Kode ICD" in item for item in form["review"]["needs_attention"])
        )

    def test_document_icd_is_not_flagged(self):
        items = CLAIM_ITEMS + [
            _item("icd_code", "S43", group="d1", family="diagnosis", start=320)
        ]
        form = build_claim_form(_contract(items), pages=PAGES_FORM)
        self.assertEqual(form["medical"]["icd_codes"], ["S43"])
        self.assertFalse(
            any("Kode ICD" in item for item in form["review"]["needs_attention"])
        )

    def test_suggestions_stay_outside_icd_codes(self):
        contract = _contract(CLAIM_ITEMS)
        contract["analyst_analysis"]["icd_suggestions"] = [
            {"suggested_icd_code": "S43", "status": "derived"}
        ]
        form = build_claim_form(contract, pages=PAGES_FORM)
        self.assertEqual(form["medical"]["icd_codes"], [])
        self.assertEqual(len(form["medical"]["icd_recommendations"]), 1)
        self.assertEqual(form["medical"]["icd_recommendations"][0]["status"], "derived")


class ClaimCaseTypeTests(unittest.TestCase):
    def test_review_labels_are_not_claim_type_categories(self):
        self.assertIsNone(canonical_claim_case_type("SP4"))
        self.assertIsNone(canonical_claim_case_type("Tinjau ulang"))

    def test_custom_rules_drive_claim_visit_and_document_classification(self):
        from schemas.claim_rulebook import (
            ClaimRulebook,
            ClaimTypeRule,
            DocumentRules,
            VisitRules,
        )

        rulebook = ClaimRulebook(
            claim_types=[ClaimTypeRule(value="completeness", labels=["manual audit"])],
            visit=VisitRules(inpatient_labels=["hospital stay"]),
            documents=DocumentRules(
                inpatient_document_types=["stay_note"],
                inpatient_text_markers=["admitted overnight"],
            ),
        )
        self.assertEqual(
            canonical_claim_case_type("Manual Audit", rulebook),
            ("completeness", ("manual audit",)),
        )
        self.assertEqual(
            canonical_visit_type("Hospital Stay", rulebook), "inpatient"
        )
        self.assertEqual(
            visit_type_from_documents(["stay_note"], rulebook=rulebook)[0],
            "inpatient",
        )

    def test_completeness_claim_is_recognized(self):
        items = [_item("claim_type", "Kelengkapan", group="c1", start=0)]
        form = build_claim_form(_contract(items))
        self.assertEqual(form["claim"]["claim_case_type"], "completeness")

    def test_reimbursement_claim_is_a_new_claim(self):
        self.assertEqual(
            canonical_claim_case_type("REIMBURSEMENT"), ("new", ("reimbursement",))
        )

    def test_document_completion_label_is_recognized(self):
        self.assertEqual(
            canonical_claim_case_type("Kelengkapan Dokumen")[0], "completeness"
        )

    def test_document_inventory_derives_new_for_skd_or_claim_form(self):
        for document_type in ("SKD", "Formulir Pengajuan Klaim"):
            with self.subTest(document_type=document_type):
                pages = [{"page_number": 1, "raw_text": document_type}]
                classifications = [{
                    "page": "1",
                    "document_type": document_type,
                    "category": None,
                    "source": "model",
                }]
                form = build_claim_form(
                    _contract([]), pages=pages, classifications=classifications
                )
                self.assertEqual(form["claim"]["claim_case_type"], "new")

    def test_complete_named_inventory_without_claim_form_is_completeness(self):
        pages = [
            {"page_number": 1, "raw_text": "Invoice"},
            {"page_number": 2, "raw_text": "Resume Medis"},
        ]
        classifications = [
            {"page": "1", "document_type": "Invoice", "source": "model"},
            {"page": "2", "document_type": "Resume Medis", "source": "model"},
        ]
        grouped = build_grouped_claim_form(
            _contract([]), pages=pages, classifications=classifications
        )
        self.assertEqual(grouped["decisions"]["claimType"]["value"], "completeness")
        self.assertTrue(grouped["isKelengkapan"])

    def test_unclassified_page_does_not_imply_completeness(self):
        pages = [
            {"page_number": 1, "raw_text": "Invoice"},
            {"page_number": 2, "raw_text": ""},
        ]
        classifications = [
            {"page": "1", "document_type": "Invoice", "source": "model"},
            {"page": "2", "document_type": None, "source": "not_named"},
        ]
        grouped = build_grouped_claim_form(
            _contract([]), pages=pages, classifications=classifications
        )
        self.assertEqual(grouped["decisions"]["claimType"]["value"], "unknown")



class DescriptionWiringTests(unittest.TestCase):
    def test_ambiguous_fields_carry_a_description(self):
        from services.claim_fields import field_descriptions

        descriptions = field_descriptions()
        for name in (
            "account_no", "reason_of_visit", "hospitalization_date",
            "amount_incurred", "exchange_rate", "sub_claim_no", "client_no",
        ):
            self.assertIn(name, descriptions)
            self.assertTrue(descriptions[name].strip())

    def test_self_explanatory_fields_stay_without_description(self):
        from services.claim_fields import field_descriptions

        descriptions = field_descriptions()
        for name in ("policy_holder_name", "address", "diagnosis"):
            self.assertNotIn(name, descriptions)

    def test_output_schema_description_reaches_the_payload(self):
        from langextract.providers.schemas.openai import OpenAISchema
        from services.langextract_service import build_extraction_schema

        schema = build_extraction_schema()
        sent = OpenAISchema.from_schema_dict(schema).response_format
        field_schema = sent["json_schema"]["schema"]["properties"]["extractions"][
            "items"
        ]["properties"]["claim_fact_attributes"]["properties"]["field_name"]
        self.assertIn("description", field_schema)
        self.assertIn("account_no", field_schema["description"])

    def test_prompt_lists_field_meanings(self):
        from services.langextract_service import build_prompt

        prompt = build_prompt()
        self.assertIn("Field meanings", prompt)
        self.assertIn("account_no :", prompt)
        self.assertIn("Nomor rekening tujuan pembayaran.", prompt)

    def test_prompt_still_contains_every_allowed_field(self):
        from services.claim_fields import claim_fields
        from services.langextract_service import build_prompt

        prompt = build_prompt()
        for name in claim_fields():
            self.assertIn(name, prompt)

    def test_registry_rejects_a_description_on_a_derived_field(self):
        from dataclasses import replace

        import services.claim_fields as module

        derived = next(spec for spec in module.FIELD_SPECS if spec.kind == "derived")
        broken = replace(derived, description="should not be allowed")
        index = next(
            position
            for position, spec in enumerate(module.FIELD_SPECS)
            if spec.name == derived.name
        )
        patched = module.FIELD_SPECS[:index] + (broken,) + module.FIELD_SPECS[index + 1:]
        saved = module.FIELD_SPECS
        module.FIELD_SPECS = patched
        try:
            errors = module.validate_registry()
        finally:
            module.FIELD_SPECS = saved
        self.assertTrue(any("must not set description" in error for error in errors))


class GroupedSlimTests(unittest.TestCase):
    def test_document_titles_do_not_conflict_with_claim_type_category(self):
        claim_type_items = [
            _item(
                "claim_type",
                "FORMULIR KLAIM MANFAAT HIDUP – ASURANSI INDIVIDU",
                group="ct1",
            ),
            _item("claim_type", "formulir klaim", group="ct2"),
            _item(
                "claim_type",
                "FORMULIR KLAIM MANFAAT HIDUP – ASURANSI INDIVIDU",
                group="ct3",
            ),
            _item("claim_type", "Formulir Klaim", group="ct4"),
            _item("claim_type", "SURAT KETERANGAN DOKTER", group="ct5"),
            _item("claim_type", "SURAT KETERANGAN DOKTER", group="ct6"),
        ]
        contract = _contract(claim_type_items)

        self.assertEqual(
            contract["analyst_analysis"]["claim_case_type"]["value"], "new"
        )
        self.assertEqual(
            contract["analyst_analysis"]["claim_case_type"]["method"],
            "explicit_claim_type_mapping",
        )
        self.assertEqual(
            contract["analyst_analysis"]["claim_case_type"]["evidence"],
            [
                "FORMULIR KLAIM MANFAAT HIDUP – ASURANSI INDIVIDU",
                "formulir klaim",
                "FORMULIR KLAIM MANFAAT HIDUP – ASURANSI INDIVIDU",
                "Formulir Klaim",
            ],
        )

    def test_legacy_grouped_conflict_is_refreshed_from_saved_facts(self):
        items = [
            _item("claim_type", "FORMULIR KLAIM MANFAAT HIDUP – ASURANSI INDIVIDU", group="ct1"),
            _item("claim_type", "SURAT KETERANGAN DOKTER", group="ct2"),
        ]
        contract = _contract(items)
        contract["analyst_analysis"].pop("decisions", None)
        contract["analyst_analysis"]["claim_case_type"] = {
            "value": "unknown",
            "method": "conflicting_explicit_claim_type_values",
            "evidence": ["formulir klaim", "SURAT KETERANGAN DOKTER"],
        }
        old_grouped = build_grouped_claim_form(contract)
        self.assertEqual(old_grouped["decisions"]["claimType"]["value"], "unknown")

        refreshed = refresh_grouped_decisions(contract, old_grouped)
        self.assertEqual(refreshed["decisions"]["claimType"]["value"], "new")
        self.assertEqual(refreshed["isNewClaim"], True)

    def test_major_flags_have_explainable_decisions(self):
        contract = _contract([
            _item("provider_name", "Mount Elizabeth Hospital", group="p1", family="provider"),
            _item("visit_type", "Rawat Jalan", group="v1"),
            _item("claim_type", "REIMBURSEMENT", group="c1"),
            _item("currency", "SGD", group="f1", family="invoice"),
            _item("discharge_date", "2026-08-01", group="d1"),
            _item("invoice_date", "2026-09-05", group="i1", family="invoice"),
        ])
        grouped = build_grouped_claim_form(contract)
        decisions = grouped["decisions"]

        for name in (
            "visitType", "isRI", "isRJ", "claimType", "isNewClaim",
            "isKelengkapan", "isOverseas", "possibleNewClaim",
        ):
            self.assertEqual(
                set(decisions[name]),
                {"value", "confidence", "method", "reason", "evidence"},
            )
            self.assertTrue(decisions[name]["reason"])

        self.assertTrue(grouped["isRJ"])
        self.assertTrue(grouped["isNewClaim"])
        self.assertTrue(grouped["isOverseas"])
        self.assertTrue(grouped["possibleNewClaim"])
        self.assertEqual(decisions["isRJ"]["value"], True)
        self.assertEqual(decisions["possibleNewClaim"]["value"], True)

    def test_insufficient_evidence_is_unknown_not_a_confident_negative(self):
        grouped = build_grouped_claim_form(_contract([]))
        decisions = grouped["decisions"]

        for name in ("isRI", "isRJ", "isOverseas", "possibleNewClaim"):
            self.assertIsNone(decisions[name]["value"])
            self.assertIsNone(decisions[name]["confidence"])
            self.assertTrue(decisions[name]["reason"])

    def test_grouped_matches_screenshot_parents(self):
        contract = _contract(IDENTITY_ITEMS + CLAIM_ITEMS)
        grouped = build_grouped_claim_form(contract)
        # MEMBER INFO (screenshot 1)
        self.assertEqual(grouped["memberInfo"]["insuredName"], "ANDRIAN YUSUP")
        self.assertEqual(grouped["memberInfo"]["dob"], "1955-12-19")
        self.assertEqual(grouped["memberInfo"]["clientNo"], "51197086")
        self.assertEqual(grouped["memberInfo"]["mobilePhone"], "6283830303987")
        # HEADER (screenshot 2): visit + provider + icd + currency + amount
        header = grouped["header"]
        self.assertEqual(header["hphpName"], "MITRA KELUARGA PONDOK TJANDRA")
        self.assertEqual(header["hpspNames"], ["Dr. Andrianto, Sp.OT"])
        self.assertEqual(header["visitFrmDtm"], "2026-08-31")
        self.assertEqual(header["visitToDtm"], "2026-08-31")
        self.assertEqual(header["incuredAmt"], 1215882)
        self.assertEqual(header["sysvCurrencyType"], "IDR")
        # POLICY (screenshot 3): hanya yang ada, hilang bila semua null
        self.assertEqual(grouped["policyInfo"]["policyHolder"], "ANDRIAN YUSUP")

    def test_empty_parents_are_dropped(self):
        grouped = build_grouped_claim_form(_contract([]))
        for parent in ("memberInfo", "header", "policyInfo", "serviceLines"):
            self.assertNotIn(parent, grouped)

    def test_multiple_doctors_all_kept(self):
        items = IDENTITY_ITEMS + CLAIM_ITEMS + [
            _item("doctor_name", "Dr. Kedua", group="p4", family="provider", start=190),
        ]
        grouped = build_grouped_claim_form(_contract(items))
        self.assertEqual(
            grouped["header"]["hpspNames"],
            ["Dr. Andrianto, Sp.OT", "Dr. Kedua"],
        )

    def test_full_json_carries_decisions_and_usage(self):
        contract = _contract(IDENTITY_ITEMS + CLAIM_ITEMS)
        contract["usage"] = {"total": {"total_tokens": 10}}
        full = build_full_claim_json(contract)
        self.assertIn("decisions", full)
        self.assertEqual(full["usage"]["total"]["total_tokens"], 10)


class RulebookSingleDoorTests(unittest.TestCase):
    def test_decision_schema_accepts_flags_and_unknown_values(self):
        from schemas.claim_rulebook import Decision

        flag = Decision(value=True, reason="Currency non-IDR.")
        unknown = Decision(value=None, reason="Tanggal belum cukup.")
        self.assertIs(flag.value, True)
        self.assertIsNone(unknown.value)

    def test_prompt_generated_from_rulebook(self):
        from schemas.claim_rulebook import ClaimRulebook, rulebook_prompt_section
        from services.langextract_service import build_prompt

        custom = ClaimRulebook()
        custom.overseas.country_currency["Vietnam"] = "VND"
        prompt = build_prompt(custom)
        self.assertIn("Vietnam", prompt)
        self.assertIn("Business rules (generated from ClaimRulebook)", prompt)
        self.assertIn(rulebook_prompt_section(custom).splitlines()[0], prompt)

    def test_rulebook_rejects_bad_confidence(self):
        from pydantic import ValidationError

        from schemas.claim_rulebook import ConfidenceTier

        with self.assertRaises(ValidationError):
            ConfidenceTier(when="x", confidence=150.0)

    def test_rules_follow_custom_rulebook(self):
        from schemas.claim_rulebook import ClaimRulebook
        from services.claim_rules import canonical_visit_type, detect_overseas

        custom = ClaimRulebook()
        custom.visit.inpatient_labels = ["opname"]
        self.assertEqual(canonical_visit_type("opname", custom), "inpatient")
        self.assertIsNone(canonical_visit_type("rawat inap", custom))
        flag, country, _reason = detect_overseas("RS Jakarta", "USD", custom)
        self.assertTrue(flag)


if __name__ == "__main__":
    unittest.main()

