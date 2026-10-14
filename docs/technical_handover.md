# Technical Handover — Claim Entry Python

**Repository:** `afg2002/claim-entry-python`  
**Branch:** `main`  
**Document status:** Technical handover / onboarding reference  
**Last reviewed:** 2026-10-14

---

## 1. Purpose

`Claim Entry Python` adalah PoC/MVP untuk memahami kumpulan dokumen klaim asuransi, mengambil fakta yang dapat ditelusuri ke dokumen sumber, melakukan normalisasi dan business assessment secara deterministik, lalu menghasilkan JSON yang dapat digunakan untuk autofill Claim Entry.

Prinsip utama sistem:

> **Document → Grounded Facts → Validated Claim Data → Claim Entry JSON**

LLM digunakan untuk document understanding dan extraction, tetapi **LLM bukan sumber kebenaran business rule**. Nilai yang masuk ke business data harus memiliki grounding/provenance atau berasal dari deterministic processing.

---

## 2. Current architecture

```text
                         PDF / Images
                              │
                              ▼
                  ┌──────────────────────┐
                  │ Document Parser      │
                  │                      │
                  │ Docling              │
                  │  - Remote VLM        │
                  │  - Local OCR         │
                  │                      │
                  │ MinerU               │
                  └──────────┬───────────┘
                             │
                             ▼
                         Markdown
                             │
                             ▼
                    ┌─────────────────┐
                    │ LangExtract     │
                    │                 │
                    │ Grounded facts │
                    │ Provenance     │
                    └────────┬────────┘
                             │
                             ▼
                    Claim Facts / Data
                             │
                             ▼
                 Python normalization /
                 grouping / validation
                             │
                             ▼
                    Analyst Analysis
                             │
                             ▼
                    Claim Form JSON
                             │
                             ▼
                       Streamlit UI
```

### Important separation

```text
OCR / VLM
   ↓
Markdown
   ↓
Extraction
   ↓
Facts
   ↓
Business rules / assessment
   ↓
Claim Entry JSON
```

Do not move business decisions into the OCR/VLM prompt unless there is a strong architectural reason. OCR/layout understanding and claim assessment are separate responsibilities.

---

## 3. Main runtime flow

The default insurance claim path is:

1. User uploads a PDF/image through Streamlit.
2. The selected parser processes the document.
3. Docling Remote VLM can perform OCR/layout understanding and produce Markdown.
4. LangExtract receives the resulting Markdown for grounded claim extraction.
5. Python services normalize, group, validate, and assemble the extracted facts.
6. Assessment/business rules derive review-oriented decisions.
7. The claim form builder produces the compact Claim Entry JSON.
8. Usage/cost information is attached to the output when available.
9. Streamlit renders parser/extraction results and diagnostics.

LangExtract normally receives **Markdown, not page images**. The legacy remote-VLM extraction path is separate and should not be confused with the default LangExtract claim path.

---

## 4. Repository structure

```text
claim-entry-python/
├── app.py
├── config.py
├── config.properties.example
├── requirements.txt
│
├── parsers/
│   ├── docling_parser.py
│   ├── mineru_parser.py
│   └── marker_parser.py
│
├── services/
│   ├── langextract_service.py
│   ├── llm_service.py
│   ├── claim_fields.py
│   ├── claim_form.py
│   ├── claim_rules.py
│   ├── assessment_service.py
│   ├── page_classifier.py
│   └── usage_tracker.py
│
├── schemas/
│   ├── claim_facts.py
│   ├── claim_pipeline.py
│   ├── claim_rulebook.py
│   └── native.py
│
├── comparison/
├── tests/
├── docs/
└── output/
```

### Responsibility map

| Component | Responsibility |
|---|---|
| `app.py` | Streamlit UI and orchestration |
| `config.py` | Configuration loading, validation, rates and cost calculation |
| `parsers/docling_parser.py` | Docling conversion, Remote VLM/local OCR, Markdown and structured document output |
| `parsers/mineru_parser.py` | Local MinerU comparison pipeline |
| `services/langextract_service.py` | Grounded extraction from Markdown |
| `services/llm_service.py` | Legacy remote-VLM structured extraction compatibility path |
| `services/claim_fields.py` | Extraction field registry/specification |
| `services/claim_form.py` | Final grouped Claim Entry JSON construction |
| `services/claim_rules.py` | Deterministic claim rules |
| `services/assessment_service.py` | Derived claim analysis and decisions |
| `services/page_classifier.py` | Document/page classification |
| `services/usage_tracker.py` | Provider usage parsing, token accounting, estimates and cost reports |
| `schemas/claim_facts.py` | Canonical fact models |
| `schemas/claim_pipeline.py` | Final contract validation |
| `schemas/claim_rulebook.py` | Single source of truth for configurable business rules |
| `tests/` | Offline regression/unit tests |

---

## 5. Document processing matrix

| Input / stage | Processor | Rationale | Output | Main use |
|---|---|---|---|---|
| PDF/image | Docling Remote VLM | Complex layout, scanned documents, tables and visual understanding | Markdown + Docling document data | Main remote document understanding path |
| PDF/image | RapidOCR/Tesseract/Docling OCR | Local OCR comparison or fallback | Markdown | Local processing / comparison |
| PDF/image | MinerU | Parser comparison and local CPU workflow | Markdown | Benchmark/comparison |
| Markdown | LangExtract | Grounded entity/fact extraction with provenance | Extraction items / claim facts | Main claim extraction |
| Claim facts | Python services | Deterministic grouping, normalization and validation | Canonical claim data | Business-safe transformation |
| Claim facts + rulebook | Assessment service | Business decisions should not be delegated to OCR | Analyst analysis | RI/RJ, overseas, completeness, review etc. |
| Canonical data | Claim form builder | Match the Claim Entry UI contract | Grouped Claim JSON | Autofill / downstream integration |

---

## 6. Docling Remote VLM

The main Remote VLM configuration is controlled through `config.properties`.

Typical settings:

```properties
BASE_URL=https://example.com/openapi
API_KEY=replace-with-your-api-key
VLM_MODEL=google/gemini-3.1-flash-lite
```

The Docling integration builds an OpenAI-compatible `/chat/completions` endpoint and supplies the configured VLM model through `ApiVlmEngineOptions`.

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

### Important

The Remote VLM is the **document understanding/OCR stage**. It is not the same thing as LangExtract's structured claim extraction model.

There can therefore be two separate remote-model stages:

```text
Stage A: Docling Remote VLM
         PDF/image → Markdown

Stage B: LangExtract provider/model
         Markdown → grounded claim facts
```

Each stage should be tracked separately for token usage and cost.

---

## 7. LangExtract extraction

The default claim extraction path uses the Markdown generated by the document stage.

```text
Docling Markdown
      ↓
LangExtract
      ↓
Grounded extraction items
      ↓
Claim facts
```

Extraction items may contain:

- extracted text
- character interval
- source excerpt
- page hint
- alignment status
- grounding information
- deterministic occurrence IDs

### Grounding rule

An LLM output without a valid source grounding interval must not automatically become business data.

This is critical because the system is designed for auditability rather than generic chatbot-style generation.

---

## 8. Facts vs analysis

The system intentionally separates document facts from derived business conclusions.

```text
                    DOCUMENT
                       │
                       ▼
                Grounded Facts
                       │
                       ▼
                Normalized Facts
                       │
                       ▼
               Business Assessment
                       │
                       ▼
                 Claim Entry JSON
```

### Facts

Examples:

- insured name
- policy number
- patient/member number
- provider name
- doctor name
- admission/discharge date
- diagnosis
- medication
- procedure
- invoice amount
- currency

### Derived decisions

Examples:

- inpatient/outpatient
- overseas claim
- new claim
- completeness/review status
- possible new claim
- other review indicators

Assessment should read canonical facts and the rulebook, not raw Markdown.

---

## 9. Rulebook-driven development

Business rules are centralized in:

```text
schemas/claim_rulebook.py
```

The rulebook is the source of truth for configurable business behavior.

When adding or changing a rule:

1. Update the rulebook model/default.
2. Consume the rule through the appropriate service.
3. Do not copy the same default value into multiple services.
4. Update prompt representation if the rule must be exposed to extraction.
5. Add regression tests.
6. Update documentation if the contract or behavior changes.

The intended dependency is:

```text
ClaimRulebook
     ↓
Prompt generation / validation / assessment
```

not:

```text
Hard-coded service constant
     ↓
Business decision
```

---

## 10. Claim Entry JSON contract

The final grouped output is intended to match the Claim Entry UI contract rather than simply exposing the raw LLM response.

Typical high-level structure:

```json
{
  "schema_version": "claim-full-v2",
  "memberInfo": {},
  "header": {},
  "serviceLines": [],
  "decisions": {},
  "usage": {}
}
```

Typical header fields include:

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

Do not casually rename these fields to generic extraction names. The final JSON is an integration/UI contract.

---

## 11. Data integrity rules

### No hallucinated business values

A value should come from:

- grounded document extraction,
- deterministic normalization,
- or an explicitly documented derived rule.

### Missing values remain missing

Prefer `null`, an empty collection, or the contract's documented empty representation rather than inventing a value.

### Derived values remain distinguishable

A calculated field should not be represented as if it were explicitly printed in the document.

### ICD recommendations are not ICD codes

Suggested terminology belongs in the recommendation/analysis area and requires validation. A suggestion must not overwrite an explicit ICD code from the source document.

---

## 12. Token usage and cost tracking

Token tracking is implemented in:

```text
services/usage_tracker.py
```

The tracker supports two sources:

### Provider-reported usage

Actual usage returned by the model gateway/provider.

Canonical fields:

```json
{
  "input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0,
  "cached_tokens": 0,
  "total_tokens": 0
}
```

### Local estimation

When a provider does not return usage, the application can use `tiktoken` to estimate text token counts.

This is explicitly marked as estimated and **must not be treated as an actual provider bill**.

### Source values

The usage report can identify:

```text
provider
estimated
mixed
none
```

### Docling Remote VLM usage

The current tracker attempts to aggregate usage from Docling's conversion result, page by page, through the VLM prediction object.

Conceptually:

```text
ConversionResult
  └── pages[]
       └── predictions
            └── vlm_response
                 └── usage
```

The usage tracker normalizes common provider shapes such as:

```text
prompt_tokens / completion_tokens / total_tokens
```

and equivalent Gemini-style names.

### Cost calculation

Per-stage rates are configured separately:

```properties
USD_TO_IDR=18000
VLM_INPUT_RATE_PER_MILLION=0.25
VLM_OUTPUT_RATE_PER_MILLION=1.50
LANGEXTRACT_INPUT_RATE_PER_MILLION=0.25
LANGEXTRACT_OUTPUT_RATE_PER_MILLION=1.50
```

This allows VLM OCR and LangExtract extraction to be reported independently before being combined into a document total.

### Important handover note

When investigating a token/cost discrepancy, first inspect:

```json
"source": "provider"
```

versus:

```json
"source": "estimated"
```

Do not compare estimated token counts directly with provider billing without checking the source.

---

## 13. Configuration reference

Main configuration areas:

| Setting | Purpose |
|---|---|
| `BASE_URL` | OpenAI-compatible Remote VLM gateway |
| `API_KEY` | Remote VLM credential |
| `VLM_MODEL` | Docling Remote VLM model |
| `EXTRACTION_MODEL` | Legacy remote extraction model |
| `LANGEXTRACT_PROVIDER` | LangExtract provider |
| `LANGEXTRACT_MODEL` | LangExtract model |
| `LANGEXTRACT_BASE_URL` | LangExtract gateway when applicable |
| `LANGEXTRACT_API_KEY` | LangExtract credential |
| `LANGEXTRACT_MAX_CHAR_BUFFER` | Extraction chunk size |
| `LANGEXTRACT_BATCH_LENGTH` | Batch size |
| `LANGEXTRACT_MAX_WORKERS` | Extraction concurrency |
| `LANGEXTRACT_EXTRACTION_PASSES` | Number of extraction passes |
| `LANGEXTRACT_USE_OUTPUT_SCHEMA` | Provider output constraints |
| `LANGEXTRACT_FALLBACK_TO_LEGACY` | Legacy extractor fallback |
| `USAGE_ESTIMATE_FALLBACK` | Estimate missing usage with tiktoken |
| `VLM_INPUT_RATE_PER_MILLION` | VLM input pricing |
| `VLM_OUTPUT_RATE_PER_MILLION` | VLM output pricing |
| `LANGEXTRACT_INPUT_RATE_PER_MILLION` | Extraction input pricing |
| `LANGEXTRACT_OUTPUT_RATE_PER_MILLION` | Extraction output pricing |
| `USD_TO_IDR` | USD → IDR conversion used for reporting |

Use `config.properties.example` as the starting point. Never commit real API keys.

---

## 14. Streamlit application

Start the application with:

```powershell
python -m streamlit run app.py
```

The UI provides parser comparison controls and extraction-related options. The Streamlit app is an orchestration layer; business logic should remain in services/schemas rather than being duplicated inside UI callbacks.

### Recommended debugging approach

When a result is unexpected:

1. Confirm the selected parser.
2. Confirm the OCR engine.
3. Inspect the generated Markdown.
4. Inspect extraction/provenance data.
5. Inspect canonical claim data.
6. Inspect assessment/decision output.
7. Inspect final Claim Entry JSON.
8. Inspect `usage` and its `source`.

Do not debug only from the final JSON because an upstream parser or grounding issue can look like a business-rule problem.

---

## 15. Legacy extraction path

There is still a compatibility path in the codebase for direct remote-VLM structured extraction.

Conceptually:

```text
Markdown + page images
       ↓
Legacy remote VLM
       ↓
Native schema
       ↓
Structured extraction
```

The default insurance claim path is intended to use LangExtract instead. `LANGEXTRACT_FALLBACK_TO_LEGACY=false` is the recommended default because silent fallback can make a failed extraction appear successful.

If the legacy path is enabled for troubleshooting, treat its usage and output as a separate stage.

---

## 16. Error handling and troubleshooting

### Remote VLM request errors

Check:

- `BASE_URL`
- `/chat/completions` endpoint compatibility
- `API_KEY`
- `VLM_MODEL`
- gateway/model availability
- request timeout
- provider response body

### Empty Remote VLM Markdown

The Docling parser contains a fallback path when the Remote VLM conversion produces empty Markdown. This fallback should be visible in diagnostics because it means the requested VLM stage did not produce usable document content.

### JSON parsing errors

Typical causes:

- provider returned malformed JSON,
- extraction unit is too large,
- output schema constraints were not enforced by the provider,
- model did not follow the requested structure.

Mitigation:

- keep extraction units bounded,
- use output schema where supported,
- keep prompts/rules deterministic,
- do not silently convert malformed output into valid-looking business data.

### Token usage missing

If usage is missing:

1. Check `usage.source`.
2. Check whether provider response actually contains a `usage` block.
3. Inspect the Docling `ConversionResult`/VLM prediction structure.
4. Determine whether the current number is provider-reported or tiktoken-estimated.
5. Only then adjust the usage tracker.

---

## 17. Testing

Run the offline test suite:

```powershell
python -m unittest discover tests -v
```

The suite covers claim contracts, normalization, claim-form generation, usage tracking and other core behaviors. Live API smoke tests require valid credentials/configuration.

### Before changing extraction behavior

At minimum test:

- a clean digital PDF,
- a scanned document,
- a multi-page document,
- a document with tables,
- a document with missing fields,
- malformed/partial provider output,
- provider usage present,
- provider usage absent.

---

## 18. Development workflow

Before committing a change:

```powershell
python -m unittest discover tests -v
python -m streamlit run app.py
```

When adding a new claim field, update the relevant layers together:

```text
Field registry
    ↓
Extraction mapping/schema
    ↓
Normalization
    ↓
Claim facts
    ↓
Assessment if applicable
    ↓
Claim form mapping
    ↓
Tests
    ↓
Documentation
```

Avoid creating a second independent field registry in a service just because it is convenient locally.

---

## 19. Operational checklist

### First-time setup

- [ ] Python 3.12+ available.
- [ ] Virtual environment created.
- [ ] `requirements.txt` installed.
- [ ] `config.properties` created from the example.
- [ ] API credentials configured locally.
- [ ] VLM model verified with the configured gateway.
- [ ] LangExtract provider/model verified.
- [ ] Offline tests pass.
- [ ] Streamlit starts successfully.

### Before production-like testing

- [ ] Use representative claim documents.
- [ ] Verify Markdown quality before debugging extraction.
- [ ] Verify provenance for important fields.
- [ ] Verify final JSON field names against Claim Entry UI.
- [ ] Verify token usage source is `provider` when actual provider usage is required.
- [ ] Check cost rates are current.
- [ ] Check no secrets are committed.

---

## 20. Known limitations

1. **Provider behavior is not uniform.** Usage fields can differ by gateway/model.
2. **Token estimates are not billing statements.** Image tokenization cannot be reconstructed reliably from text alone.
3. **Page provenance depends on parser output.** Character offsets are the stronger primary grounding signal when page markers are unavailable.
4. **ICD suggestions are not medical coding validation.** Human/terminology validation remains necessary.
5. **Remote VLM output quality depends on the scanned document and provider/model behavior.**
6. **Legacy extraction exists for compatibility.** It should not silently replace the default claim extraction path.
7. **Large documents require bounded extraction/chunking.** Increasing concurrency without considering provider rate limits can increase failures rather than throughput.
8. **Parser comparison is not automatically a quality benchmark.** A proper benchmark requires a labelled regression dataset and field-level metrics.

---

## 21. Current improvement direction

The next major optimization area is a **Pre-VLM cost gate** before Remote VLM:

```text
PDF / Image
     ↓
Cheap OCR
     ↓
Regex / RapidFuzz mapping
     ↓
 ┌───────────────┐
 │ confident     │
 │ enough?       │
 └───────┬───────┘
     yes │ no
         │
         ▼
      skip VLM       Remote VLM
         │               │
         └───────┬───────┘
                 ▼
              Markdown
                 ↓
             LangExtract
```

This work is being developed separately from `main` and should **not be considered part of the main-branch production contract until merged and validated**.

The intended goal is not to replace Remote VLM. The goal is to reduce unnecessary Remote VLM calls while preserving the existing extraction pipeline for ambiguous/low-confidence documents.

When this optimization is eventually merged, token usage should be reported as:

```text
Without Pre-VLM
    Remote VLM tokens

With Pre-VLM
    Cheap OCR cost
    + Remote VLM tokens for documents that still need VLM
    = total cost
```

The comparison must use provider-reported usage whenever available.

---

## 22. Handover notes for the next developer

If you are taking over this project, read these files first:

1. `README.md` — product/architecture overview.
2. `flow.md` — current architecture and engineering rules.
3. `docs/langextract_claim_developer_guide.md` — extraction-specific details.
4. `schemas/claim_rulebook.py` — business-rule source of truth.
5. `services/claim_fields.py` — extraction field registry.
6. `services/claim_form.py` — final Claim Entry mapping.
7. `parsers/docling_parser.py` — document conversion and VLM path.
8. `services/usage_tracker.py` — token/cost accounting.
9. `app.py` — Streamlit orchestration.
10. `tests/` — expected behavior before changing implementation.

### Golden rules

- **Do not make the LLM the business-rule engine.**
- **Do not treat ungrounded extraction as a document fact.**
- **Do not rename Claim Entry fields casually.**
- **Do not hide provider failures behind silent fallback.**
- **Do not call estimated token usage actual billing usage.**
- **Do not duplicate rule/field registries across services.**
- **Do not optimize token cost before measuring actual provider usage.**
- **Keep facts, assessment, and UI contract separate.**

---

## 23. Useful commands

```powershell
# Install dependencies
uv pip install -r requirements.txt

# Run tests
python -m unittest discover tests -v

# Start Streamlit
python -m streamlit run app.py

# Inspect git status
git status

# Create a feature branch
git checkout -b feature/<name>
```

---

## 24. Reference documents

- [`README.md`](../README.md) — project overview and quick start.
- [`flow.md`](../flow.md) — architecture, rulebook and development guidance.
- [`docs/langextract_claim_developer_guide.md`](./langextract_claim_developer_guide.md) — extraction developer guide.
- [`docs/claim_rulebook.md`](./claim_rulebook.md) — documented claim rules.
- [`config.properties.example`](../config.properties.example) — configuration template.

---

## 25. Final handover summary

The project is best understood as a **document intelligence pipeline**, not a single LLM call:

```text
PDF / Image
    ↓
Docling / MinerU
    ↓
Markdown
    ↓
LangExtract
    ↓
Grounded Claim Facts
    ↓
Deterministic normalization + rulebook
    ↓
Assessment
    ↓
Claim Entry JSON
    ↓
UI / downstream integration
```

The most important engineering properties to preserve are:

1. **Grounding** — every important fact should be traceable.
2. **Separation of concerns** — OCR, extraction, business logic and UI are different layers.
3. **Deterministic business rules** — rules belong in Python/rulebook, not hidden in prompts.
4. **Stable output contract** — final JSON must match the Claim Entry UI.
5. **Usage transparency** — provider-reported and estimated token usage must remain distinguishable.
6. **Explicit failures** — a failed model call must not become apparently valid claim data.

This document describes the `main` branch baseline. Experimental optimization work should remain clearly separated until it has tests, live validation, cost measurements and a reviewed merge.