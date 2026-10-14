# Claim Entry Python

> **AI-assisted document understanding for insurance claims** — parse messy claim documents, extract grounded facts, validate them, and produce a clean structured claim form.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Docling](https://img.shields.io/badge/Parser-Docling-111827)](https://github.com/docling-project/docling)
[![LangExtract](https://img.shields.io/badge/Extraction-LangExtract-111827)](https://github.com/google/langextract)
[![License](https://img.shields.io/badge/License-Internal-lightgrey)](#license)

## Why this project?

Insurance claim documents rarely arrive as one clean, structured form. A single submission can contain scanned forms, receipts, medical reports, prescriptions, invoices, and supporting documents.

**Claim Entry Python** turns that document bundle into structured, auditable data without treating the LLM as the source of truth.

The core principle is simple:

> **Extract facts from the document first. Derive business insights separately. Never invent missing values.**

---

## ✨ Highlights

- 📄 **Multi-parser document pipeline** — Docling and MinerU can be compared from the UI.
- 👁️ **OCR + layout understanding** — remote VLM or local OCR depending on the selected pipeline.
- 🧩 **Grounded extraction** — LangExtract works on the complete Markdown produced by the document stage.
- 🔎 **Provenance-first** — extracted values retain source spans, offsets, alignment status, and page hints where available.
- 🧠 **Deterministic business logic** — grouping, normalization, validation, and derived fields are handled in Python.
- 🏥 **Claim-aware schema** — member, policy, claim, visit, provider, medical, financial, medication, procedure, and laboratory data.
- 💰 **Usage & cost tracking** — reported and estimated token usage are kept separate.
- 🛡️ **No silent fallback by default** — extraction failures do not quietly become apparently valid claim data.
- 🧪 **Offline test suite** — core tests do not require network/API access.
- 📦 **Audit-friendly output** — compact claim-form JSON plus the complete contract are preserved.

---

# 📚 Technical Handover & Developer Guide

This README is the primary technical handover for the project. It intentionally keeps the important development instructions in one place so a new developer does not need to hunt through multiple documents.

## 1. Architecture

```text
                         ┌──────────────────┐
                         │   PDF / Images   │
                         └────────┬─────────┘
                                  │
                                  ▼
                    ┌──────────────────────────┐
                    │ Document Parsing / OCR   │
                    │                          │
                    │ Docling Remote VLM       │
                    │            OR            │
                    │ MinerU / Local OCR       │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                         ┌───────────────┐
                         │    Markdown   │
                         └───────┬───────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │       LangExtract        │
                    │                          │
                    │ Grounded claim facts    │
                    │ + source spans          │
                    └────────────┬─────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │ Python Claim Pipeline    │
                    │                          │
                    │ Grouping                 │
                    │ Normalization            │
                    │ Validation               │
                    │ Derived analysis         │
                    └────────────┬─────────────┘
                                 │
                  ┌──────────────┴──────────────┐
                  ▼                             ▼
          ┌───────────────┐             ┌────────────────┐
          │ Claim Facts   │             │ Claim Form JSON│
          │ + provenance  │             │ compact output │
          └───────────────┘             └────────────────┘
```

### Separation of responsibilities

```text
OCR / VLM
   ↓
Markdown
   ↓
Extraction
   ↓
Grounded Facts
   ↓
Normalization / Grouping
   ↓
Business Rules / Assessment
   ↓
Claim Entry JSON
```

Do not move business decisions into the OCR/VLM prompt unless there is a strong architectural reason. Document understanding and claim assessment are separate responsibilities.

---

## 2. Main runtime flow

The default insurance-claim path is:

1. User uploads a PDF/image through Streamlit.
2. The selected parser processes the document.
3. Docling Remote VLM can perform OCR/layout understanding and produce Markdown.
4. LangExtract receives the resulting Markdown for grounded claim extraction.
5. Python services normalize, group, validate, and assemble extracted facts.
6. Assessment/business rules derive review-oriented decisions.
7. The claim form builder produces the compact Claim Entry JSON.
8. Usage/cost information is attached to the output when available.
9. Streamlit renders parser/extraction results and diagnostics.

LangExtract normally receives **Markdown, not page images**.

There are two conceptually different model stages:

```text
Stage A — Document understanding
PDF/Image → Docling Remote VLM / OCR → Markdown

Stage B — Claim extraction
Markdown → LangExtract → Grounded Claim Facts
```

Track their token usage and cost separately.

---

## 3. Repository structure

```text
claim-entry-python/
├── app.py                         # Streamlit application / orchestration
├── config.py                      # Application configuration
├── config.properties.example      # Configuration template
├── requirements.txt               # Python dependencies
│
├── parsers/
│   ├── docling_parser.py          # Docling pipeline
│   ├── mineru_parser.py           # MinerU pipeline
│   └── marker_parser.py           # Marker parser
│
├── services/
│   ├── langextract_service.py     # Grounded claim extraction
│   ├── llm_service.py             # Legacy / remote VLM extraction
│   ├── claim_fields.py            # Claim field definitions
│   ├── claim_form.py              # Claim-form builder
│   ├── claim_rules.py             # Deterministic rules
│   ├── assessment_service.py      # Derived claim analysis
│   ├── page_classifier.py         # Page classification
│   └── usage_tracker.py           # Token / cost accounting
│
├── schemas/
│   ├── claim_facts.py             # Claim fact models
│   ├── claim_pipeline.py          # Final contract validation
│   ├── claim_rulebook.py          # Single source of truth for rules
│   └── native.py                  # Native schemas
│
├── comparison/                    # Parser comparison / metrics
├── tests/                          # Offline test suite
├── docs/                           # Historical/supporting docs if retained
└── output/                         # Generated artifacts
```

### Responsibility map

| Component | Responsibility |
|---|---|
| `app.py` | Streamlit UI and orchestration |
| `config.py` | Configuration, validation, rates and cost settings |
| `parsers/docling_parser.py` | Docling conversion, Remote VLM/local OCR, Markdown output |
| `parsers/mineru_parser.py` | Local MinerU comparison pipeline |
| `services/langextract_service.py` | Grounded extraction from Markdown |
| `services/llm_service.py` | Legacy direct remote-VLM structured extraction |
| `services/claim_fields.py` | Extraction field registry/specification |
| `services/claim_form.py` | Final Claim Entry JSON construction |
| `services/claim_rules.py` | Deterministic rule execution |
| `services/assessment_service.py` | Derived claim analysis and decisions |
| `services/page_classifier.py` | Document/page classification |
| `services/usage_tracker.py` | Provider usage parsing, token accounting and cost reports |
| `schemas/claim_facts.py` | Canonical fact models |
| `schemas/claim_pipeline.py` | Final contract validation |
| `schemas/claim_rulebook.py` | Configurable business-rule source of truth |
| `tests/` | Regression and unit tests |

---

## 4. Document processing

| Input | Processor | Output | Main purpose |
|---|---|---|---|
| PDF/image | Docling Remote VLM | Markdown + document data | Complex layout, scanned documents, tables |
| PDF/image | RapidOCR/Tesseract/Docling OCR | Markdown | Local OCR / fallback / comparison |
| PDF/image | MinerU | Markdown | Local parser comparison |
| Markdown | LangExtract | Grounded extraction items | Claim fact extraction |
| Claim facts | Python services | Canonical claim data | Normalization, grouping, validation |
| Claim facts + rulebook | Assessment service | Decisions/analysis | RI/RJ, overseas, completeness, review |
| Canonical data | Claim form builder | Claim Entry JSON | Autofill / downstream integration |

### Docling Remote VLM

Typical configuration:

```properties
BASE_URL=https://example.com/openapi
API_KEY=replace-with-your-api-key
VLM_MODEL=google/gemini-3.1-flash-lite
```

Conceptually:

```text
PDF / image
    ↓
Docling VlmPipeline
    ↓
Remote VLM API
    ↓
Docling document
    ↓
Markdown
```

The Remote VLM is the **document understanding/OCR stage**. It is not the same as the LangExtract claim-extraction model.

### MinerU on Windows

For CPU Windows environments, use the `pipeline` backend. The project does not rely on `mineru-api` or a remote VLM for the local comparison path.

---

## 5. Extraction: Markdown, JSON, and other formats

The project has several output layers. Do not treat them as interchangeable.

```text
PDF / Image
    ↓
Document Parser
    ↓
Markdown
    ↓
LangExtract
    ↓
Grounded Extraction Items
    ↓
Claim Facts
    ↓
Validated / Derived Data
    ↓
Claim Entry JSON
```

### 5.1 Extracting a document to Markdown

Markdown is the output of the **document-understanding stage**. It is useful when the goal is to inspect what the parser/VLM understood before claim extraction.

```text
PDF/Image
   ↓
Docling / MinerU / OCR
   ↓
Markdown
   ↓
output/*.md
```

The Markdown should preserve the document content and structure as faithfully as the selected parser allows: headings, paragraphs, tables, lists, and page markers when available.

**Important:** do not filter the Markdown before sending it to LangExtract. The default extraction path uses the complete Markdown so that provenance offsets remain meaningful.

### 5.2 Extracting to JSON

There are several JSON concepts:

| JSON layer | Meaning |
|---|---|
| Raw extraction | Direct grounded extraction items |
| `claim_data` | Raw claim facts with provenance |
| `normalized_claim_data` | Parsed/normalized values |
| `analyst_analysis` | Derived business conclusions |
| Claim Entry JSON | Final integration/autofill contract |

Example final shape:

```json
{
  "schema_version": "claim-form-v1",
  "member_info": {
    "insured_name": "John Doe",
    "patient_id": "000123"
  },
  "policy_info": {
    "policy_number": "POL-001"
  },
  "visit": {
    "type_of_visit": "outpatient",
    "status": "derived"
  },
  "medical": {
    "reason_of_visit": "...",
    "icd_codes": [],
    "icd_recommendations": []
  },
  "financial": {
    "amount_incurred": 1500000,
    "currency": "IDR"
  },
  "review": {
    "needs_attention": []
  }
}
```

### 5.3 Other export formats

When another consumer needs a different format, build it from canonical claim data instead of scraping the final UI JSON.

```text
                    Canonical Claim Facts
                             │
             ┌───────────────┼───────────────┐
             ↓               ↓               ↓
          Markdown          JSON          CSV/XLSX
             │               │               │
          Review/API      Integration     Analysis
```

Typical uses:

| Format | Use case |
|---|---|
| `.md` | Human-readable parser output / debugging |
| `.json` | API, integration, Claim Entry autofill |
| `.csv` | Flat reporting / analysis |
| `.xlsx` | Analyst review / business reporting |
| `.txt` | Plain-text downstream processing |
| `.html` | Web preview |

Do not create a second business-logic implementation for every export format. Keep the transformation layer deterministic and based on canonical data.

---

## 6. Grounded extraction and provenance

Extraction items may contain:

- `extraction_text`
- `char_start`
- `char_end`
- source excerpt
- page hint when available
- alignment status
- grounding status
- deterministic extraction ID

### Grounding rule

An LLM output without a valid source grounding interval must not automatically become business data.

Values should come from one of:

1. grounded document extraction;
2. deterministic normalization;
3. explicitly documented derived rules.

Missing values stay `null`, `[]`, or the contract's documented empty representation.

---

# 7. Developer guide: adding a new field

This is the most important section for developers extending the project.

## 7.1 Field extension flow

```text
New field requirement
       ↓
Field allowlist / specification
       ↓
Raw claim facts
       ↓
Normalization
       ↓
Grouping / relationship (if needed)
       ↓
Final schema / Claim Entry mapping
       ↓
Tests
       ↓
README / contract documentation
```

### Step 1 — Add the field to the extraction allowlist

Edit:

```text
services/langextract_service.py
```

Add the field to `CLAIM_FIELDS`:

```python
CLAIM_FIELDS = (
    # existing fields...
    "provider_tax_id",
)
```

The extraction prompt and output schema are generated from the field registry, so do not duplicate the same field name in several prompts.

### Step 2 — Decide whether it is scalar or list

A single value belongs to the scalar field set.

```text
patient_id       → scalar
provider_name    → scalar
invoice_no       → scalar
```

Repeated values belong to a list/record structure.

```text
medications      → list
procedures       → list
lab_results      → list
```

Do not add derived business values to the document-fact allowlist.

### Step 3 — Add a field description when the name is ambiguous

Field descriptions are used to explain ambiguous fields to the model.

Good:

```python
"provider_tax_id": "Tax identification number of the healthcare provider."
```

Do not add verbose descriptions to obvious fields when the existing registry intentionally omits them to reduce prompt cost.

### Step 4 — Add normalization when required

Edit the normalization logic in `services/langextract_service.py` or the relevant normalization service.

Examples:

```text
Raw:
"Rp 986.882"

Normalized:
986882
```

Keep both layers:

```python
claim_data["total_amount"] = "Rp 986.882"
normalized_claim_data["total_amount"] = 986882
```

Never destroy the raw source value just because a normalized value exists.

For dates, amounts, currency, IDs and other structured values, use the existing normalizers rather than writing ad-hoc parsing in the UI.

### Step 5 — Add grouping if the field belongs to a record

For fields that belong together, update the relationship configuration.

Typical locations:

```text
FAMILY_BY_FIELD
_grouped_records(...)
build_examples()
*_records output
```

Example:

```python
medication_records = _grouped_records(
    grounded_items,
    family="medication",
    fields=("medication", "dosage", "frequency", "duration"),
    anchor_field="medication",
)
```

`group_id` from the LLM is only a hint. Python creates canonical occurrence IDs so two occurrences of the same text at different positions are not incorrectly merged.

### Step 6 — Update the final schema when the field is public

If the field is part of the public/application contract, update:

```text
schemas/claim_pipeline.py
```

Define its type, nullability, format/pattern and relationship as appropriate.

Do not turn a document fact into a business enum just because the UI uses an enum. Domain decisions belong in validation/assessment.

### Step 7 — Map it to Claim Entry JSON if required

If the field must appear in the autofill payload, update:

```text
services/claim_form.py
```

The final Claim Entry field name is an integration contract. Do not casually rename it to match the internal extraction field.

### Step 8 — Add regression tests

At minimum test:

- raw value is preserved;
- leading zeros are preserved for identifiers;
- normalization is correct;
- provenance contains offsets/source excerpt;
- missing field becomes `null`/`[]`;
- duplicate text at different spans remains separate occurrences;
- ungrounded extraction does not become a fact;
- derived values do not enter `claim_data`.

Run:

```powershell
python -m unittest tests.test_langextract_service -v
```

### Step 9 — Update this README

If the field changes the output contract or developer workflow, update the relevant field/contract section in this README.

### Field checklist

```text
[ ] CLAIM_FIELDS
[ ] scalar/list classification
[ ] FIELD_SPECS / description if needed
[ ] normalization
[ ] grouping/relationship if needed
[ ] final schema
[ ] Claim Entry mapping if needed
[ ] tests
[ ] README / contract
```

---

# 8. Developer guide: adding or changing business rules

## 8.1 One source of truth

All configurable business rules belong in:

```text
schemas/claim_rulebook.py
```

The rulebook is a Pydantic model and should be treated as the single source of truth.

The intended dependency is:

```text
ClaimRulebook
      ↓
Prompt generation
      ↓
Schema / validation
      ↓
Rule execution
      ↓
Assessment
```

Do **not** create a second hard-coded copy of the same business rule in a service or prompt.

## 8.2 Adding a new rule

Example requirement:

> If a provider location is Vietnam and currency is not explicitly present, use VND as the fallback currency.

Edit `schemas/claim_rulebook.py`:

```python
class OverseasRule(BaseModel):
    country_currency: dict[str, str] = Field(
        default={
            "Singapore": "SGD",
            "Korea": "KRW",
            "Vietnam": "VND",
        },
        description="Fallback currency from provider location.",
    )
```

The rulebook then feeds prompt generation, schema validation and business-rule execution through the existing architecture.

### Existing rule categories

The rulebook currently covers concepts such as:

- claim case type (`new`, completeness, review);
- visit type (RI/RJ) and visit-period behavior;
- document category → visit type mapping;
- overseas/country/currency fallback;
- receipt period after discharge;
- name similarity threshold;
- confidence tiers for derived visit decisions.

### Rule priority

Explicit document facts must win over fallback/inferred values.

Example:

```text
Explicit currency in document
        ↓
       WIN
        ↓
Country-based currency fallback
```

Fallback logic must never overwrite an explicit source value.

## 8.3 Rules versus extraction

Do not add a business decision to `CLAIM_FIELDS` just because it is needed in the final output.

For example, these are **derived** values:

```text
risk_score
is_fraud
recommended_visit_type
possibleNewClaim
suggested_icd_code
```

They do not belong in raw document facts.

Instead, calculate them in the assessment layer and represent them as derived data, for example:

```python
{
    "value": value,
    "status": "derived",
    "confidence": confidence,
    "confidence_type": "heuristic_not_calibrated",
    "evidence": [evidence_text],
    "evidence_refs": [extraction_id],
}
```

## 8.4 Adding a derived rule

Typical flow:

```text
Grounded Claim Facts
       ↓
Normalization
       ↓
ClaimRulebook
       ↓
services/claim_rules.py
       ↓
services/assessment_service.py
       ↓
analyst_analysis / decisions
```

Assessment should read canonical facts and the rulebook, **not raw Markdown**.

## 8.5 Test the rule

For every rule, test at least:

```text
positive case
negative case
missing input
explicit value overriding fallback
boundary condition
```

Run the relevant tests, for example:

```powershell
python -m unittest tests.test_claim_form tests.test_langextract_service tests.test_usage_tracker -v
```

### Rule checklist

```text
[ ] rule added to ClaimRulebook
[ ] description explains business meaning
[ ] no duplicated hard-coded rule
[ ] explicit source values take precedence
[ ] assessment consumes canonical facts
[ ] positive/negative/boundary tests added
[ ] output contract updated if necessary
[ ] README updated
```

---

# 9. Claim Entry JSON contract

The final grouped output is intended to match the Claim Entry UI contract rather than simply exposing the raw LLM response.

Typical high-level structure:

```json
{
  "schema_version": "claim-form-v1",
  "document": {},
  "member_info": {},
  "policy_info": {},
  "claim": {},
  "visit": {},
  "provider": {},
  "medical": {},
  "financial": {},
  "service_lines": [],
  "medications": [],
  "procedures": [],
  "lab_results": [],
  "review": {},
  "usage": {}
}
```

Typical Claim Entry fields include:

```text
insuredName
accountNo
accountName
typeOfVisit
visitFrmDtm
visitToDtm
noOfDays
hphpName
hphpId
hpspNames
reasonOfVists
icdCode1
icdCode2
incuredAmt
sysvCurrencyType
exchangeRate
invoiceNo
internalRemark
```

### Important contract rules

- Do not casually rename final integration fields.
- Missing values stay empty/null according to the contract.
- Derived values remain distinguishable from explicit facts.
- `icd_recommendations` are not the same as `icd_codes`.
- Empty parent objects can be pruned only according to the established output contract.
- `decisions` should include a reason/evidence so developers and analysts can understand why a decision was made.

---

# 10. Token usage and cost tracking

Token tracking is implemented in:

```text
services/usage_tracker.py
```

Each model stage should be tracked separately.

Canonical usage fields include:

```json
{
  "input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "cached_tokens": 0,
  "total_tokens": 0
}
```

### Provider versus estimated usage

The usage report can identify:

```text
provider
estimated
mixed
none
```

- `provider` = actual usage returned by the provider/gateway.
- `estimated` = local token estimate, typically used when provider usage is unavailable.
- `mixed` = report contains multiple sources.
- `none` = no usage information available.

Estimated usage is **not an actual provider bill**.

### Adding a new model stage

Use `UsageLedger` rather than manually summing values:

```python
from services.usage_tracker import UsageLedger, build_usage_report

ledger = UsageLedger()
ledger.record(response.usage)

usage = build_usage_report(
    ledger,
    stage="tahap_baru",
    model=model_name,
    provider="openai",
    usd_to_idr=config.usd_to_idr,
    input_rate_per_million=input_rate,
    output_rate_per_million=output_rate,
)
```

If the provider does not return usage, do not invent it. `normalize_usage` can return `None`; estimation is allowed only through the configured fallback mechanism.

When adding a new stage, also update the UI/metrics stage labels used by the project so the stage appears in usage reporting and benchmark tables.

### Cost configuration

Typical settings:

```properties
USD_TO_IDR=18000
VLM_INPUT_RATE_PER_MILLION=0.25
VLM_OUTPUT_RATE_PER_MILLION=1.50
LANGEXTRACT_INPUT_RATE_PER_MILLION=0.25
LANGEXTRACT_OUTPUT_RATE_PER_MILLION=1.50
```

When debugging a cost discrepancy, inspect the usage `source` before comparing numbers with provider billing.

---

# 11. Configuration

Copy the template:

```powershell
Copy-Item config.properties.example config.properties
```

Important settings include:

| Variable | Purpose |
|---|---|
| `BASE_URL` | OpenAI-compatible Remote VLM gateway |
| `API_KEY` | Remote VLM credential |
| `VLM_MODEL` | Docling Remote VLM model |
| `LANGEXTRACT_PROVIDER` | Extraction provider: `openai`, `gemini`, or `ollama` |
| `LANGEXTRACT_MODEL` | LangExtract model |
| `LANGEXTRACT_BASE_URL` | LangExtract gateway when applicable |
| `LANGEXTRACT_API_KEY` | LangExtract credential |
| `LANGEXTRACT_MAX_CHAR_BUFFER` | Extraction chunk size |
| `LANGEXTRACT_BATCH_LENGTH` | Batch size |
| `LANGEXTRACT_MAX_WORKERS` | Extraction concurrency |
| `LANGEXTRACT_EXTRACTION_PASSES` | Number of extraction passes |
| `LANGEXTRACT_CONTEXT_WINDOW_CHARS` | Optional previous-context window |
| `LANGEXTRACT_USE_OUTPUT_SCHEMA` | Provider schema/output constraints |
| `LANGEXTRACT_ENABLE_ICD_SUGGESTIONS` | Isolated ICD suggestion pass |
| `LANGEXTRACT_FALLBACK_TO_LEGACY` | Legacy extractor fallback |
| `USAGE_ESTIMATE_FALLBACK` | Estimate missing usage locally |
| `VLM_INPUT_RATE_PER_MILLION` | VLM input pricing |
| `VLM_OUTPUT_RATE_PER_MILLION` | VLM output pricing |
| `LANGEXTRACT_INPUT_RATE_PER_MILLION` | Extraction input pricing |
| `LANGEXTRACT_OUTPUT_RATE_PER_MILLION` | Extraction output pricing |
| `USD_TO_IDR` | USD → IDR reporting conversion |

Recommended starting values:

```properties
LANGEXTRACT_MAX_CHAR_BUFFER=2000
LANGEXTRACT_MAX_WORKERS=2
LANGEXTRACT_BATCH_LENGTH=2
LANGEXTRACT_EXTRACTION_PASSES=1
LANGEXTRACT_USE_OUTPUT_SCHEMA=true
LANGEXTRACT_FALLBACK_TO_LEGACY=false
USAGE_ESTIMATE_FALLBACK=true
```

Never commit real API keys.

---

# 12. Streamlit application

Start the application:

```powershell
python -m streamlit run app.py
```

The UI is an orchestration layer. Business logic should remain in services/schemas rather than being duplicated in Streamlit callbacks.

### Debugging an unexpected result

Follow the pipeline in order:

```text
1. Selected parser
       ↓
2. OCR / VLM engine
       ↓
3. Generated Markdown
       ↓
4. Extraction items + provenance
       ↓
5. claim_data
       ↓
6. normalized_claim_data
       ↓
7. analyst_analysis / decisions
       ↓
8. Claim Entry JSON
       ↓
9. usage / cost
```

Do not debug only from the final JSON. An upstream parser or grounding problem can look like a business-rule problem.

---

# 13. Legacy extraction path

There is a compatibility path for direct remote-VLM structured extraction:

```text
Markdown + page images
       ↓
Legacy remote VLM
       ↓
Native schema
       ↓
Structured extraction
```

The default insurance-claim path is intended to use LangExtract. `LANGEXTRACT_FALLBACK_TO_LEGACY=false` is recommended because silent fallback can make a failed extraction appear successful.

If the legacy path is enabled for troubleshooting, treat its usage and output as a separate stage.

---

# 14. Pre-VLM cost gate

An experimental optimization branch contains a cheap pre-VLM mapping stage using OCR + Regex + RapidFuzz before Remote VLM.

Conceptually:

```text
PDF / Image
    ↓
Cheap OCR
    ↓
Regex + RapidFuzz
    ↓
Confidence sufficient?
   ├── YES → skip Remote VLM
   └── NO  → Remote VLM
```

Important: the **Remote VLM remains the main document-understanding pipeline when the cheap gate is not confident**. The optimization is intended to reduce unnecessary remote-VLM calls, not replace document understanding completely.

The current experimental branch also has a known optimization opportunity: reuse the pre-flight OCR result when the gate succeeds instead of running OCR twice.

---

# 15. Data integrity rules

### No hallucinated business values

A business value must come from grounded extraction, deterministic normalization, or an explicitly documented derived rule.

### Missing values stay missing

Never invent a value simply because the output schema contains the field.

### Raw and normalized values are separate

Example:

```text
claim_data.total_amount
    = "Rp 986.882"

normalized_claim_data.total_amount
    = 986882
```

### Derived values remain derived

A calculated value must not be presented as if it was printed in the document.

### ICD recommendations are not ICD codes

A recommendation belongs in the recommendation/analysis area and requires validation. It must never overwrite an explicit ICD code from the source document.

### Explicit source values beat fallbacks

```text
Document explicitly says SGD
        ↓
       SGD

Country fallback says MYR
        ↓
      ignored
```

---

# 16. Error handling & troubleshooting

## Remote VLM request errors

Check:

- `BASE_URL`
- `/chat/completions` compatibility
- `API_KEY`
- `VLM_MODEL`
- provider/gateway availability
- timeout
- provider response body

## Empty Remote VLM Markdown

Inspect the Docling conversion result and fallback diagnostics. Empty Markdown means the requested document-understanding stage did not produce usable content.

## JSON parsing errors

Typical causes:

- provider returned malformed JSON;
- extraction unit is too large;
- model ignored the requested schema;
- output contains unsupported characters/escaping;
- prompt contains ambiguous or duplicated instructions.

Useful mitigations:

- enable provider output schema where supported;
- reduce extraction chunk size;
- reduce extraction concurrency when rate limits are involved;
- keep business rules out of the raw extraction task;
- inspect the raw provider response before changing downstream code.

## Wrong field / wrong value

Debug in this order:

```text
Markdown
  ↓
Grounding
  ↓
Raw claim_data
  ↓
Normalization
  ↓
Grouping
  ↓
Rule / assessment
  ↓
Final JSON
```

Do not immediately change the prompt if the value was already extracted correctly and was later corrupted by normalization or business logic.

---

# 17. Testing

Run the offline suite:

```powershell
python -m unittest discover tests -v
```

For focused development:

```powershell
python -m unittest tests.test_langextract_service -v
python -m unittest tests.test_claim_form -v
python -m unittest tests.test_usage_tracker -v
```

Core tests should not require network/API access.

### What should be tested

**Fields:**

- raw values;
- leading zeros;
- normalization;
- provenance;
- missing fields;
- duplicate occurrences;
- ungrounded extraction rejection.

**Rules:**

- positive case;
- negative case;
- missing input;
- explicit value versus fallback;
- boundary condition.

**Output:**

- schema validity;
- Claim Entry field names;
- decisions and reasons;
- usage source;
- stable handling of empty values.

---

# 18. Development workflow

Before opening a change:

```powershell
# Run tests
python -m unittest discover tests -v

# Start the app
python -m streamlit run app.py
```

When changing a field or rule, update the implementation and tests together.

### Golden rules for maintainers

1. **Document is the source of truth.**
2. **Ground first, derive second.**
3. **Do not hallucinate missing values.**
4. **Keep business rules deterministic where possible.**
5. **Keep ClaimRulebook as the single source of configurable rules.**
6. **Do not duplicate field names or rule constants across prompts/services without a clear reason.**
7. **Keep raw and normalized data separate.**
8. **Keep facts and analysis separate.**
9. **Keep provider-reported and estimated usage separate.**
10. **Add regression tests whenever extraction, rule, or output behavior changes.**
11. **Treat the Claim Entry JSON shape as an integration contract.**
12. **Update this README when the developer workflow or contract changes.**

---

## 🚀 Quick start

### 1. Clone

```powershell
git clone https://github.com/afg2002/claim-entry-python.git
cd claim-entry-python
```

### 2. Create virtual environment

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```powershell
python -m pip install --upgrade pip uv
uv pip install -r requirements.txt
```

### 4. Configure

```powershell
Copy-Item config.properties.example config.properties
```

Edit `config.properties` with the required provider/API configuration.

### 5. Run

```powershell
python -m streamlit run app.py
```

---

## 🗺️ Roadmap

- [ ] Stronger document/page classification
- [ ] More robust provenance visualization
- [ ] Better parser comparison metrics
- [ ] Claim-level confidence scoring
- [ ] Human review workflow
- [ ] Terminology/ICD validation integration
- [ ] Regression dataset and benchmark suite
- [ ] Production deployment profile
- [ ] Reuse pre-VLM OCR output to avoid duplicate OCR work

---

## License

This repository currently does not declare an open-source license. Treat the code as **all rights reserved / project-internal** unless a license is added to the repository.

---

<div align="center">

**Claim Entry Python**

Document → Grounded Facts → Validated Claim Data → Claim Entry JSON

Built with Python, Docling, LangExtract, and Streamlit.

</div>
