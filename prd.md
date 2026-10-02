# PRD — Architecture Report: Document Parser Comparison (Claim Pipeline)

> STATUS: dokumentasi objektif kondisi existing PERSIS seperti kode saat ini.
> Bagian 1–10 dan 12–13 murni deskriptif (tanpa rekomendasi). Bagian 11 berisi
> daftar **kandidat** refactor yang belum diputuskan, karena bagian arsitektur
> sudah lengkap. Sesuai permintaan: pahami dulu, refactor nanti.
>
> Cara membaca: bagian 9 = penilaian maintainability, bagian 10 = pain point,
> bagian 11 = kandidat perbaikan (belum ada keputusan), bagian 12 = cara
> memverifikasi setiap klaim di dokumen ini.

Workspace: `document-parser-comparison-streamlit-v2`
Tanggal kajian: 2026-10-02
Cakupan file: `app.py`, `config.py`, `parsers/*`, `services/*`,
`schemas/*`, `comparison/*`, `utils/*`, `tests/*`, `docs/*`.

## 1. End-to-End Processing Flow (as implemented)

### 1.1 Jalur utama (default claim flow)

```text
PDF
 ↓  (upload → nama internal aman, utils/file_utils.py)
Parser: Docling Remote VLM ATAU MinerU pipeline lokal
 ↓  OCR / layout / table → Markdown
Markdown penuh (tanpa keyword filtering)
 ↓  build_prompt()          [services/langextract_service.py]
 ↓  + rulebook_prompt_section() [schemas/claim_rulebook.py]
LangExtract (grounded extraction items, tanpa gambar halaman)
 ↓  prepare_intermediate_items() → canonical occurrence IDs
 ↓  build_claim_contract()  [services/langextract_service.py]
Claim contract (claim-fact-v1):
 claim_data + normalized_claim_data + provenance +
 validation + analyst_analysis
 ↓  classify_pages()        [services/page_classifier.py]
 ↓  build_claim_form()      [services/claim_form.py] → claim-form-v1
 ↓  build_grouped_claim_form() / build_full_claim_json()
     → claim-full-v2 (memberInfo/header/policyInfo/serviceLines
        + decisions + provenance ringkas + usage)
 ↓  app.py render: benchmark, grouped JSON, usage, log, debug prompt, download
 ↓  Artifacts: *.md, *.json, *.full.json
```

### 1.2 Jalur legacy / non-claim (fallback eksplisit)

```text
Markdown + page images
 ↓  extract_with_remote_vlm() [services/llm_service.py]
     per-page extract → deterministic python merge → finalize ClaimAnalysis
 ↓  information_extraction (bentuk legacy, tanpa LangExtract contract)
```

Jalur ini hanya dipakai bila profil non-claim dipilih atau LangExtract
gagal dan `LANGEXTRACT_FALLBACK_TO_LEGACY=true` (default false → gagal
keras, bukan fallback diam-diam).

### 1.3 Detail per tahap

1. OCR/VLM output
   - Docling Remote VLM (`parsers/docling_parser.py::parse_docling`,
     `_convert_document`, `_vlm_usage`): Markdown via
     `document.export_to_markdown()` + `raw_data` via
     `export_to_dict()/model_dump()`. Prompt OCR internal Docling
     (preset granite_vision), tidak ada string prompt publik.
     Fallback ke `docling_default_fallback` bila Markdown kosong.
   - MinerU lokal (`parsers/mineru_parser.py::parse_mineru`):
     subprocess `mineru -p ... -b pipeline`, baca artifact `.md`/`.json`
     terbaik. Tidak ada VLM/API MinerU. Usage VLM =
     `unavailable_usage_report` (bukan nol).
   - Marker/OpenDataLoader (`parsers/marker_parser.py`,
     `parsers/opendataloader_parser.py`): parser pembanding, tidak
     masuk jalur claim LangExtract.
2. Markdown generation: sumber tunggal kebenaran untuk extraction.
   LangExtract menerima SELURUH Markdown + `prompt_description` +
   `examples` + opsional output schema. Tidak ada filter keyword,
   tidak ada gambar halaman.
3. LangExtract usage (`services/langextract_service.py::
   extract_claim_from_markdown`): resolve model/provider, hitung
   `source_characters`, `build_prompt()`, `build_extraction_schema()`,
   `build_examples()`, panggil `lx.extract`, `prepare_intermediate_items`,
   `build_claim_contract` (+ saran ICD opsional via
   `_icd_suggestion_config`), ledger usage via
   `services/usage_tracker.py`.
4. Pydantic schemas: `schemas/native.py` (kontrak legacy/LLM),
   `schemas/claim_pipeline.py` (kontrak claim-fact-v1),
   `schemas/claim_rulebook.py` (rulebook 1 pintu),
   `services/page_classifier.py::PageClassification/PageInventory`.
5. Rulebook usage: `rulebook_prompt_section()` disuntik sebagai
   item 14 `build_prompt()`; fungsi rules menerima argumen
   `rulebook` opsional (default `DEFAULT_RULEBOOK`).
6. JSON generation: `build_claim_form` (claim-form-v1, kompat),
   `build_grouped_claim_form` / `build_full_claim_json`
   (claim-full-v2, slim per screenshot + usage). Parser menulis
   `*.json` (claim_form + grouped_claim_form +
   information_extraction + usage) dan `*.full.json`
   (Docling: contract penuh; MinerU: grouped slim — lihat bagian 2.1).
7. Claim assessment flow: `derive_claim_analysis()` +
   `_validate_claim()` di dalam `build_claim_contract()`;
   `_visit_block()` + `_reason_of_visit()` di `claim_form.py`
   saat proyeksi; render + benchmark di `app.py`.

## 2. Current Architecture — Layer Inventory

Setiap lapisan di bawah ini adalah kondisi nyata saat ini (nama file, nama
fungsi, jumlah baris) tanpa penilaian.

| # | Layer | Tanggung jawab | Input | Output | Kelas / fungsi utama | Dependensi utama |
|---|-------|----------------|-------|--------|----------------------|------------------|
| L1 | UI & orkestrasi — `app.py` (1343 baris) | Upload, konfigurasi sidebar, memilih parser, memanggil parser, merender benchmark/tab, download ZIP/JSON/Excel | File upload (PDF/gambar/office) | Streamlit UI + `st.session_state.results` | `_render_grouped_claim`, `_render_claim_form`, `_usage_cost`, `_render_usage_metrics`, `inspect_pdf_type` | `parsers`, `comparison`, `services.usage_tracker`, `utils`, `fitz`, `pdf_inspector`, `streamlit` |
| L2 | Konversi dokumen — `parsers/` (docling 476, mineru 332, opendataloader 194, marker 95 baris) | PDF → Markdown + raw OCR per halaman | Path file | `markdown` (str), `raw_ocr_pages` (list), `docling_document`/`mineru_raw` (dict) | `parse_docling`, `_convert_document`, `_raw_ocr_pages_from_docling`, `_detect_document_profile`, `_vlm_usage`, `parse_mineru`, `parse_marker`, `parse_opendataloader` | `docling`, `services.langextract_service`, `services.llm_service`, `schemas.native`, `services.page_classifier`, `services.claim_form`, `services.usage_tracker`, `config` |
| L3 | Claim extraction (LangExtract) — `services/langextract_service.py` (2009 baris) | Markdown → grounded items → claim contract | `markdown`, `AppConfig` | `claim-fact-v1` contract + `usage` + `debug_prompts` + `artifacts` | `extract_claim_from_markdown`, `build_prompt`, `build_extraction_schema`, `build_examples`, `serialize_langextract_result`, `prepare_intermediate_items`, `build_claim_contract`, `derive_claim_analysis`, `_validate_claim`, `_classify_visit_type`, `_classify_claim_case_type`, `_run_icd_suggestion_pass` | `langextract`, `services.claim_fields`, `services.claim_rules`, `services.usage_tracker`, `config` |
| L4 | Registry field extracted — `services/claim_fields.py` (508 baris) | Satu tabel `FieldSpec` untuk semua nama field, tipe, deskripsi prompt, definisi grup relasi | — (modul konstan) | `FIELD_SPECS`, `GROUP_DEFINITIONS`, helper query + `validate_registry()` | `FieldSpec`, `GroupDefinition`, `claim_fields`, `scalar_fields`, `list_destinations`, `field_descriptions`, `group_definitions`, `validate_registry` | stdlib saja |
| L5 | Rulebook 1 pintu — `schemas/claim_rulebook.py` (264 baris) | Sumber tunggal nilai bisnis + `Field(description=...)` yang menjadi prompt/schema validasi | — (default Python) | `DEFAULT_RULEBOOK` + `rulebook_prompt_section()` + JSON Schema Pydantic | `ClaimRulebook`, `ClaimTypeRule`, `VisitRules`, `DocumentRules`, `OverseasRule`, `PeriodRules`, `NameCheckRule`, `ConfidenceTier`, `Decision`, `ClaimSignals`, `validate_rulebook` | `pydantic` |
| L6 | Adapter rule deterministik — `services/claim_rules.py` (292 baris) | Fungsi murni: kanonikalisasi label, deteksi overseas, similarity nama, judul dokumen, visit type dari dokumen | Nilai mentah + opsional `rulebook` | Nilai kanonis + evidence | `canonical_visit_type`, `canonical_claim_case_type`, `visit_type_from_documents`, `detect_overseas`, `overseas_currency_fallback`, `name_similarity_pct`, `document_title`, `refresh_rule_cache` | `schemas.claim_rulebook` (lazy import) |
| L7 | Klasifikasi halaman — `services/page_classifier.py` (302 baris) | Beri nama setiap halaman via model (OpenAI-compatible), kategori sempit untuk logika RI/RJ | `raw_ocr_pages`, `AppConfig` | 1 entri per halaman (`document_type`, `category`, `title`, `source`) | `PageClassification`, `PageInventory`, `classify_pages`, `build_page_classification_prompt`, `_response_schema`, `_build_page_digest`, `_call_model` | `openai`, `config`, `services.claim_rules.document_title` |
| L8 | Proyeksi form — `services/claim_form.py` (798 baris) | Contract → `claim-form-v1` (verbose per blok) dan `claim-full-v2` (slim grouped per screenshot) | Contract + pages + classifications | JSON form / grouped + `decisions` + `review` + `usage` | `build_claim_form`, `build_grouped_claim_form`, `build_full_claim_json`, `_visit_block`, `_reason_of_visit`, `_service_lines`, `_prune_parent`, `_pick`, `_decisions_from_analysis`, `_member_block`, `_policy_block`, `_claim_block`, `_provider_block`, `_medical_block`, `_financial_block`, `_page_inventory` | `services.claim_rules`, `datetime` |
| L9 | Jalur legacy LLM — `services/llm_service.py` (834 baris) | Fallback per-halaman VLM + merge deterministik + finalize `ClaimAnalysis` | Markdown + page images + schema model | `information_extraction` legacy + usage | `extract_with_remote_vlm`, `_extract_single_page`, `_merge_page_results`, `_finalize_claim_analysis`, `_normalize_claim_output`, `_legacy_usage_report` | `openai`, `schemas.native`, `services.usage_tracker`, `config` |
| L10 | Usage & cost — `services/usage_tracker.py` (621 baris) | Ledger `reported` vs `estimated`, laporan per tahap, proxy perekam usage SDK | payload usage mentah / teks untuk estimasi | Report seragam (`input_tokens`, `source`, `cost`, `note`) | `UsageLedger`, `normalize_usage`, `build_usage_report`, `unavailable_usage_report`, `combine_usage_reports`, `usage_from_vlm_prediction`, `vlm_usage_from_conversion` | `tiktoken` (opsional), `config.calculate_cost` |
| L11 | Benchmark — `comparison/` (comparator 13, metrics 69 baris) | Tabel metrik per parser + matriks kemiripan Markdown | `results` | `pandas.DataFrame` | `compare_results`, `calculate_metrics`, `result_usage`, `usage_stage_reports`, `similarity_matrix`, `_nodes`, `_labels`, `_size` | `pandas`, `services.usage_tracker` |
| L12 | Konfigurasi — `config.py` (255 baris) | Baca `config.properties` + env, validasi mode, tarif & kurs | File properties + env | `AppConfig` | `AppConfig.from_file`, `validate_for_llm`, `validate_for_langextract`, `rates_for`, `calculate_cost` | stdlib |
| L13 | Utilitas — `utils/` (file_utils 217, pdf_converter 218 baris) | Nama internal aman (Windows), deteksi kompatibilitas parser, konversi PDF | Nama file / bytes | Nama aman, tabel `PARSER_FORMAT_SUPPORT` | `create_safe_internal_filename`, `get_compatible_parsers`, `get_incompatible_parsers`, `is_image_file`, `get_file_extension` | `fitz`/`pypdf`, stdlib |
| L14 | Schema kontrak — `schemas/` | Bentuk kontrak yang divalidasi/di-serialize | dict | Pydantic model + JSON Schema | `native.py`: `NATIVE_SCHEMA_REGISTRY`, `ClaimAnalysis`, `InsuranceClaim`; `claim_pipeline.py`: `ClaimContract`, `AnalystAnalysis`, `VisitTypeAnalysis`, `ClaimCaseTypeAnalysis`, `CLAIM_CONTRACT_JSON_SCHEMA`, `validate_claim_contract` | `pydantic` |

### 2.1 Catatan penting soal tiga artefak JSON

| Artefak | Ditulis oleh | Isi |
|---------|--------------|-----|
| `{stem}.json` | Docling & MinerU | Bundel lengkap: `claim_form`, `grouped_claim_form`, `information_extraction`, `docling_document`/`mineru_raw`, `raw_ocr_pages`, `debug_prompts`, `usage` |
| `{stem}.full.json` | Docling | **Contract penuh** (`information_extraction`) — `parsers/docling_parser.py:432` |
| `{stem}.full.json` | MinerU | **grouped slim** bila ada, jika tidak contract — `parsers/mineru_parser.py:186-196` |

Perbedaan isi `.full.json` antar parser adalah kondisi nyata saat ini, bukan
kesalahan penulisan dokumen ini.

### 2.2 Dua tipe payload JSON claim yang hidup berdampingan

| Payload | Versi | Kunci pembeda | Dipakai untuk |
|---------|-------|---------------|----------------|
| `claim_form` | `claim-form-v1` | `document`, `member_info`, `policy_info`, `claim`, `visit`, `provider`, `medical`, `financial`, `service_lines`, `medications`, `procedures`, `lab_results`, `review` | Kompatibilitas asli, ditampilkan dalam expander "Claim form lama (kompatibilitas)" |
| `grouped_claim_form` | `claim-full-v2` | `isNewClaim`, `isKelengkapan`, `isRI`, `isRJ`, `isOverseas`, `possibleNewClaim`, `riConfidence`, `rjConfidence`, `decisions`, `memberInfo`, `header`, `policyInfo`, `serviceLines`, `provenance`, `review`, `usage` | Render utama per screenshot, siap autofill `editForm` |

`build_full_claim_json()` (`claim_form.py:776`) saat ini **thin wrapper**: ia
mendelegasikan langsung ke `build_grouped_claim_form()` tanpa transformasi
tambahan. Secara objek fungsi keduanya berbeda
(`build_full_claim_json is build_grouped_claim_form` → `False`), tetapi
hasilnya identik.

## 3. Runtime Flow (diagram aktual)

```text
[User] --upload--> app.py
   |-- create_safe_internal_filename (utils/file_utils)
   |-- inspect_pdf_type (pdf_inspector) + preview halaman 1 (fitz)
   |
   |-- for each selected parser (Docling | MinerU):
   |      parsers.parse_docling / parse_mineru
   |         |-- konversi:  Docling Remote VLM | RapidOCR | Tesseract | default
   |         |               MinerU subprocess `mineru -b pipeline`
   |         |-- markdown  = document.export_to_markdown()  (atau artifact .md)
   |         |-- raw_data  = export_to_dict() / model_dump() / mineru json
   |         |-- raw_ocr_pages = _raw_ocr_pages_from_docling(...)  (MinerU: 1 page)
   |         |-- detection = _detect_document_profile(markdown)
   |         |   profile   = _resolve_extraction_profile(detection, markdown)
   |         |-- if profile == "Insurance claim":
   |         |      services.extract_claim_from_markdown(markdown, config)
   |         |         |-- build_prompt() + rulebook_prompt_section()
   |         |         |-- build_extraction_schema()  (provider OpenAI/Gemini)
   |         |         |-- build_examples()
   |         |         |-- _make_usage_recording_model() -> lx.extract(...)
   |         |         |-- serialize_langextract_result -> prepare_intermediate_items
   |         |         |-- build_claim_contract()
   |         |         |      |-- _build_normalized_claim_data()
   |         |         |      |-- _validate_claim()
   |         |         |      |-- derive_claim_analysis()
   |         |         |      |    |-- _classify_visit_type()
   |         |         |      |    |-- _classify_claim_case_type()
   |         |         |      |-- (opsional) _run_icd_suggestion_pass()
   |         |         |-- build_usage_report(stage="langextract")
   |         |      ELSE (non-claim, atau fallback eksplisit):
   |         |         services.extract_with_remote_vlm(page_texts, page_images...)
   |         |-- classify_pages(raw_ocr_pages, config)   [model call terpisah]
   |         |-- build_claim_form(contract, pages, classifications)
   |         |-- build_full_claim_json(contract+usage, pages, classifications)
   |         |-- tulis .md, .json, .full.json (+ jsonl langextract/intermediate)
   |
   |-- results.append(result)
   |-- compare_results(results) -> DataFrame metrik
   |-- render tab: Raw OCR | Markdown | JSON | Usage | Kemiripan | Log | Debug prompt | Download
```

Aturan percabangan yang benar-benar ada di kode:

* `effective_profile == "Insurance claim"` → LangExtract (jalur default).
* `config.langextract_fallback_to_legacy = False` (default) → kegagalan
  LangExtract langsung `raise`; nilai `True` mengaktifkan jalur legacy.
* `config.langextract_use_output_schema = True` + provider bukan
  OpenAI/Gemini → `LangExtractConfigurationError`.
* `information_extraction=False` atau `claim_data` kosong → `claim_form` dan
  `grouped_claim_form` bernilai `None` (tidak ditulis).
* `ocr_engine == "Remote VLM"` dan Markdown kosong → fallback internal Docling
  (`docling_default_fallback`), dan stage VLM ditandai tidak terbilled.

## 4. Rule Flow — di mana setiap aturan benar-benar hidup

Aturan bisnis saat ini tersebar di **tujuh kanal berbeda**. Tabel berikut
memetakan kanal ke lokasi konkret.

| # | Kanal | Lokasi konkret | Bentuk | Siapa yang menegakkan |
|---|-------|----------------|--------|------------------------|
| K1 | **Rulebook model** (1 pintu) | `schemas/claim_rulebook.py` → `ClaimTypeRule`, `VisitRules`, `DocumentRules`, `OverseasRule`, `PeriodRules`, `NameCheckRule`, `ConfidenceTier` | Nilai Python (string, tuple, float, `Field(description=...)`) | `DEFAULT_RULEBOOK`; dikonsumsi prompt (item 14), validasi, dan UI |
| K2 | **Prompt template rulebook** | `schemas/claim_rulebook.py::rulebook_prompt_section()`; diinjeksi di `langextract_service.build_prompt()` item 14 | Teks prompt yang digenerate dari K1 | LangExtract (model) |
| K3 | **Prompt inti extraction** | `services/langextract_service.py::build_prompt()` (1427) — instruksi per field, tipe grup, aturan "no invention" | Teks prompt statis + interpolasi `_field_guide()` (1367) | LangExtract (model) |
| K4 | **JSON Schema extraction** | `build_extraction_schema()` (1387); `_build_icd_suggestion_schema()` (1664); `page_classifier._response_schema()` (155) | JSON Schema yang ditegakkan SDK (`response_format` / `output_schema`) | Provider (server-side structured output) |
| K5 | **Deskripsi Pydantic → schema** | `services/page_classifier.py::PageClassification` (71) — seluruh guidance ada di `Field(description=...)`; model analis di `schemas/claim_pipeline.py` | Teks deskripsi yang ikut ke JSON Schema | Provider (schema turunan Pydantic) |
| K6 | **Fungsi Python deterministik** | `services/claim_rules.py`: `canonical_visit_type`, `canonical_claim_case_type`, `visit_type_from_documents`, `detect_overseas`, `overseas_currency_fallback`, `name_similarity_pct`, `document_title`, `_text_markers_in` | Fungsi murni | Kode aplikasi (setelah model) |
| K7 | **Konstanta hardcoded** | `claim_rules.py::INPATIENT_TEXT_MARKERS`/`OUTPATIENT_TEXT_MARKERS` (default), `claim_form.py::FORM_NAME_ALIASES` (43) + `DEFAULT_CURRENCY` + `DEFAULT_EXCHANGE_RATE`, `_RECORD_KEYS` (279), `langextract_service.py::_example_extraction` (1141) + `build_examples` (1163), `services/claim_fields.py::FIELD_SPECS` (51) | Literal di source | Kode aplikasi |

### 4.1 Isi rulebook (K1) per blok

`ClaimRulebook` (`schemas/claim_rulebook.py`) memuat tujuh blok aturan:

| Blok (nama field asli) | Field di dalamnya | Dipakai oleh |
|------|--------------|--------------|
| `claim_types: list[ClaimTypeRule]` | `value` (`new`/`completeness`/`review`), `labels`, `needs_formulir` | `canonical_claim_case_type`, prompt, `decisions` |
| `visit: VisitRules` | `inpatient_labels`, `outpatient_labels`, `inpatient_must_be_in_stay`, `outpatient_receipt_can_be_stay`, `resume_is_primary_for_RI` | `canonical_visit_type`, `visit_type_from_documents`, prompt |
| `documents: DocumentRules` | `inpatient_document_types`, `outpatient_document_types`, `inpatient_text_markers`, `outpatient_text_markers` | `_inpatient_docs`, `_outpatient_docs`, `visit_type_from_documents`, `_text_markers_in` |
| `overseas: OverseasRule` | `country_hints`, `country_currency`, `abroad_exception` (`warn_not_split`) | `detect_overseas`, `overseas_currency_fallback` |
| `period: PeriodRules` | `max_days_after_discharge` (default 30) | `derive_claim_analysis` → `possibleNewClaim`, `_validate_claim` → `needsAttention` |
| `name_check: NameCheckRule` | `mandatory`, `threshold_pct` (default 85.0) | `name_similarity_pct` → `analyst_analysis.name_check` |
| `confidence_tiers: list[ConfidenceTier]` | `when`, `confidence` (nullable) | `_tier()` di `langextract_service.py:785` |

Catatan: semua model memakai `ConfigDict(extra="forbid")`, sehingga nama field
yang salah ketik langsung gagal (bukan diabaikan).

Aturan "1 pintu": `ClaimRulebook` adalah sumber tunggal, dan
`rulebook_prompt_section()` (K2) menurunkan teks prompt langsung dari K1,
sehingga menambah aturan = menambah field + `Field(description=...)`.

### 4.2 Cache rulebook

`services/claim_rules.py::refresh_rule_cache()` (48) dan `_claim_case_rules()`
(34) menyimpan tuple `ClaimCaseRule` yang diturunkan dari rulebook. `_rulebook()`
(28) memakai **lazy import** untuk menghindari circular import dengan
`schemas.claim_rulebook`. Konsekuensinya: perubahan rulebook saat runtime perlu
`refresh_rule_cache()` eksplisit.

## 5. Prompt Flow

Prompt yang benar-benar dikirim ke model:

| Prompt | Pembangun | Channel guidance | Catatan penting |
|--------|-----------|------------------|-----------------|
| Extraction claim | `langextract_service.build_prompt(rulebook)` (1427) | Teks statis + `_field_guide()` + item 14 `rulebook_prompt_section()` + `build_examples()` sebagai few-shot | Menerima **seluruh Markdown**, tanpa filter kata kunci dan tanpa gambar halaman |
| Klasifikasi halaman | `page_classifier.build_page_classification_prompt()` (119) | Hanya 2 kalimat; seluruh guidance ada di `Field(description=...)` | Digest per halaman (`_build_page_digest`) sebagai user message; jawaban divalidasi `PageInventory.model_validate_json` |
| ICD suggestion | `_build_icd_suggestion_schema()` (1664) + `_build_icd_suggestion_examples()` (1679), dijalankan `_run_icd_suggestion_pass()` (1701) | Schema + examples | Pass opsional terpisah setelah extraction |

Alur prompt extraction (kondisi nyata):

```text
build_prompt(rulebook=DEFAULT_RULEBOOK)
  -> instruksi skema item (nama field, tipe, grup relasi)
  -> _field_guide(): deskripsi per field dari services/claim_fields.FIELD_SPECS
  -> aturan "jangan mengarang nilai" + aturan evidence span
  -> item 14: rulebook_prompt_section()          (K2 <- K1)
  + build_examples()          -> few-shot example items
  + build_extraction_schema() -> output_schema / response_format
  ==> lx.extract(text_or_documents=markdown, prompt_description=..., examples=..., model=...)
```

`config.langextract_use_output_schema` menentukan apakah `build_extraction_schema()`
benar-benar dipakai; bila `True` dan provider bukan OpenAI/Gemini →
`LangExtractConfigurationError` (gagal cepat, bukan menurunkan skema diam-diam).

## 6. Schema Flow

```text
contract runtime (dict)
  |  build_claim_contract()  -> menempelkan penanda versi skema kontrak
  |  ClaimContract.model_dump() (schemas/claim_pipeline.py)
  |  CLAIM_CONTRACT_JSON_SCHEMA  (dokumentasi + artefak)
  v
claim_form (claim-form-v1)          grouped_claim_form (claim-full-v2)
  |-- build_claim_form()              |-- build_grouped_claim_form()
  v                                   v
expander "kompatibilitas"            render utama (metrics + JSON per blok)
```

| Skema | File | Peran |
|-------|------|-------|
| `ClaimContract`, `AnalystAnalysis`, `VisitTypeAnalysis`, `ClaimCaseTypeAnalysis` | `schemas/claim_pipeline.py` | Bentuk kontrak yang divalidasi; `validate_claim_contract()` |
| `ClaimAnalysis`, `InsuranceClaim`, `NATIVE_SCHEMA_REGISTRY` | `schemas/native.py` | Skema native untuk legacy VLM per profil dokumen |
| `ClaimRulebook` + sub-model | `schemas/claim_rulebook.py` | Skema aturan bisnis (sekaligus sumber prompt) |
| `PageClassification`, `PageInventory` | `services/page_classifier.py` | Skema jawaban klasifikasi halaman (`response_format`) |
| `build_extraction_schema()` | `services/langextract_service.py` | JSON Schema ringkas yang dikirim ke provider untuk extraction |

Penggabungan tipe grup bersifat deterministik di aplikasi, bukan dari model:
`serialize_langextract_result` → `_repair_missing_group_ids` (281) →
`_assign_canonical_groups` (304) → `_deduplicate_items` (360). Jadi identitas
grup record tidak dipercaya mentah; ada perbaikan sebelum contract dibangun.

### 6.1 Benturan yang terlihat di skema

* `schemas/native.py` (legacy, model Pydantic per profil) dan
  `services/langextract_service.build_extraction_schema()` (JSON Schema ringkas)
  adalah dua sistem skema berbeda yang hidup berdampingan untuk tujuan serupa.
* `claim_form.py` memakai nama field internal `snake_case`, sedangkan
  `grouped_claim_form` memakai nama field `editForm` (`camelCase`) pada
  `memberInfo`/`header`/`policyInfo`/`serviceLines`; pemetaan berada di
  `FORM_NAME_ALIASES` dan di pemanggilan `_pick`/`_raw` per baris.

## 7. Decision Table

Setiap keputusan di bawah ini nyata ada di kode. Kolom "Sumber kebenaran"
menunjukkan di mana nilai/niat keputusan itu ditetapkan.

### 7.1 Keputusan tingkat klaim (analisis)

| # | Keputusan | Fungsi / lokasi | File | Sumber kebenaran |
|---|-----------|-----------------|------|------------------|
| D1 | `visit_type` = `inpatient`/`outpatient`/`unknown` | `_classify_visit_type` (803) | `services/langextract_service.py` | Label dokumen + `rulebook.visit.inpatient_labels`/`outpatient_labels`; dideskripsikan lewat prompt (K3) & `claim_fields` |
| D2 | Fallback visit type dari inventaris dokumen | `visit_type_from_documents` (257) dipanggil `_visit_block` (151) | `services/claim_rules.py`, `services/claim_form.py` | `rulebook.documents.inpatient_document_types`/`outpatient_document_types` + `inpatient_text_markers`/`outpatient_text_markers` |
| D3 | `claim_case_type` = `new`/`completeness` | `_classify_claim_case_type` (856) | `services/langextract_service.py` | `rulebook.claim_types[].labels` + `needs_formulir` |
| D4 | `isRI`, `isRJ`, `riConfidence`, `rjConfidence` | `build_grouped_claim_form` (631-651) | `services/claim_form.py` | `analyst_analysis.claim_signals`; fallback `visit_type` + `visit_type.confidence` |
| D5 | `isNewClaim`, `isKelengkapan` | `build_grouped_claim_form` (636-639) | `services/claim_form.py` | `signals` dari `derive_claim_analysis`, fallback `claim_case_type.value` |
| D6 | `isOverseas` | `detect_overseas` (178) dipanggil `derive_claim_analysis` (927) | `services/claim_rules.py`, `services/langextract_service.py` | `rulebook.overseas.country_hints` |
| D7 | `possibleNewClaim` | `derive_claim_analysis` (927) → `claim_signals` | `services/langextract_service.py` | `rulebook.period.max_days_after_discharge` + aturan derive internal |
| D8 | Currency fallback kasus overseas | `overseas_currency_fallback` (194) | `services/claim_rules.py` | `rulebook.overseas.country_currency` |
| D9 | `name_check.insured_similarity_pct` | `name_similarity_pct` (208) | `services/claim_rules.py` | Ambang `rulebook.name_check.threshold_pct` |
| D10 | Confidence tier nilai analisis | `_tier` (785) | `services/langextract_service.py` | `rulebook.confidence_tiers` |
| D11 | `needsAttention` / konflik field | `_validate_claim` (711), `_field_conflicts` (689) | `services/langextract_service.py` | `rulebook.period.max_days_after_discharge` + aturan validasi internal |
| D12 | Saran ICD tambahan | `_run_icd_suggestion_pass` (1701) | `services/langextract_service.py` | Schema + examples ICD internal |

### 7.2 Keputusan tingkat halaman & dokumen

| # | Keputusan | Fungsi / lokasi | File | Sumber kebenaran |
|---|-----------|-----------------|------|------------------|
| D13 | `document_type` per halaman | `classify_pages` (231) → `_clean_document_type` (184) | `services/page_classifier.py` | Nama verbatim dari model; `_NON_ANSWERS` menolak jawaban kosong |
| D14 | `category` per halaman (untuk RI/RJ) | `_clean_category` (198) | `services/page_classifier.py` | Alias `DocumentCategory = Literal[...]` (31) + tuple `DOCUMENT_CATEGORIES` (41) — 7 nilai, tanpa catch-all |
| D15 | `label`/judul halaman | `document_title` (224) | `services/claim_rules.py` | Heuristik judul dari teks halaman |
| D16 | Halaman tanpa jawaban model | `_page_inventory` (105) di `claim_form.py`; `"not_named"` berasal dari `page_classifier.py:283,288` | `services/claim_form.py`, `services/page_classifier.py` | `source="page_title"` bila klasifikasi tidak jalan sama sekali; `source="not_named"` bila model gagal menamai halaman — tidak mengarang nama |
| D17 | Profil ekstraksi (Insurance claim vs native) | `_detect_document_profile`, `_resolve_extraction_profile` | `parsers/docling_parser.py` | Deteksi dari Markdown + override UI (`extraction_profile`) |

### 7.3 Keputusan nilai & proyeksi form

| # | Keputusan | Fungsi / lokasi | File | Sumber kebenaran |
|---|-----------|-----------------|------|------------------|
| D18 | Parsing amount | `_parse_amount` (496) | `services/langextract_service.py` | Parser internal (format angka) |
| D19 | Parsing tanggal | `_parse_date` (569) | `services/langextract_service.py` | Parser internal → ISO |
| D20 | Normalisasi mata uang | `_normalize_currency` (606) | `services/langextract_service.py` | Kode mata uang internal |
| D21 | `no_of_days` / length of stay | `_length_of_stay` (96) + `_visit_block` (181) | `services/claim_form.py` | Selisih tanggal; `0` untuk outpatient |
| D22 | `reason_of_visit` (narrative) | `_reason_of_visit` (206) | `services/claim_form.py` | Fakta grounded; teks eksplisit menang; status `extracted`/`derived`/`unknown` |
| D23 | Default `IDR` + kurs `1` | `_financial_block` (435), `DEFAULT_CURRENCY`, `DEFAULT_EXCHANGE_RATE` | `services/claim_form.py` | Konstanta form; ditandai `status="default"` |
| D24 | Pemetaan field ke nama `editForm` | `FORM_NAME_ALIASES` (43) + `_pick` (556) | `services/claim_form.py` | Konstanta pemetaan |
| D25 | Parent block dihilangkan bila kosong | `_prune_parent` (531) | `services/claim_form.py` | Aturan proyeksi internal |
| D26 | Penetapan grup record (service lines, meds, lab) | `_field_default_group_type` (150), `_assign_canonical_groups` (304) | `services/langextract_service.py` | Konstanta + heuristik kunci item |

### 7.4 Keputusan pipeline & biaya

| # | Keputusan | Fungsi / lokasi | File | Sumber kebenaran |
|---|-----------|-----------------|------|------------------|
| D27 | LangExtract vs legacy VLM | `parse_docling` (331-355) | `parsers/docling_parser.py` | Profil + `config.langextract_fallback_to_legacy` |
| D28 | Skema output provider dipakai/tidak | `_supports_output_schema` (1501) | `services/langextract_service.py` | `config.langextract_use_output_schema` + `config.langextract_provider` |
| D29 | Sumber isi skema LangExtract | `build_extraction_schema` (1387), `prepare_intermediate_items` (394) | `services/langextract_service.py` | `services/claim_fields.FIELD_SPECS` |
| D30 | Field yang dijelaskan ke model | `described_fields`/`undescribed_fields` (434/437), `validate_registry` (467) | `services/claim_fields.py` | Registry `FieldSpec` |
| D31 | Parser gagal / ganti engine | `_convert_document` + fallback (304-310) | `parsers/docling_parser.py` | Markdown kosong pada Remote VLM → pipeline default |
| D32 | Sumber angka usage (`provider`/`estimated`/`mixed`/`none`) | `_resolve_source` (312), `build_usage_report` (323) | `services/usage_tracker.py` | Ledger `reported_requests` vs `estimated` |
| D33 | Stage usage yang tidak dieksekusi | `unavailable_usage_report` (420) | `services/usage_tracker.py` | MinerU pipeline lokal ditandai `unavailable`, bukan nol |
| D34 | Isi `{stem}.full.json` | `parse_docling` (432) vs `parse_mineru` (186-196) | `parsers/*` | **Tidak konsisten antar parser** (lihat 2.1) |
| D35 | Nama file internal aman (Windows) | `create_safe_internal_filename` | `utils/file_utils.py` | Aturan sanitasi nama |
| D36 | Mode LangExtract (API key vs provider lain) | `_model_config` (1473), `_make_usage_recording_model` (1505) | `services/langextract_service.py` | `config.langextract_*` + `validate_for_langextract` |

## 8. Dependencies

### 8.1 Dependensi eksternal (`requirements.txt`)

| Paket | Rentang | Dipakai oleh | Catatan |
|-------|---------|--------------|---------|
| `streamlit` | `>=1.40,<2` | `app.py` (seluruh UI) | Termasuk `st.session_state`, tab, `st.status` |
| `pandas` | `>=2.2,<3` | `app.py`, `comparison/` | Tabel metrik & export Excel |
| `psutil` | `>=6,<8` | `parsers/mineru_parser.py` | Manajemen proses subprocess |
| `PyMuPDF` | `>=1.24,<2` | `app.py`, `utils/pdf_converter.py` | Preview + render halaman |
| `Pillow` | `>=10,<13` | pipeline gambar | Dependensi tidak langsung |
| `pydantic` | `>=2.8,<3` | `schemas/*`, `page_classifier.py` | Sumber JSON Schema + validasi |
| `openai` | `>=1.60,<2` | `page_classifier`, `llm_service`, LangExtract | Klien OpenAI-compatible |
| `tiktoken` | `>=0.7,<1` | `services/usage_tracker.py` | Estimasi token; opsional saat runtime |
| `langextract[openai]` | `>=1.7,<2` | `services/langextract_service.py` | Mesin extraction utama |
| `openpyxl` | `>=3.1,<4` | `app.py` | Export Excel |
| `docling` | `>=2.70,<3` | `parsers/docling_parser.py` | Konversi + preset VLM `granite_vision` |
| `opendataloader-pdf` | `>=1,<2` | `parsers/opendataloader_parser.py` | Parser alternatif |
| `mineru[all]` | `>=3,<4` | `parsers/mineru_parser.py` | Dijalankan sebagai CLI subprocess |
| `marker-pdf` | `>=2,<3` | `parsers/marker_parser.py` | Parser alternatif |

Catatan: tidak ada `pyproject.toml`, dan `pdf_inspector` diimpor `app.py`
(`app.py:12`) tetapi tidak terdaftar di `requirements.txt` — kondisi nyata saat
ini.

### 8.2 Kopling internal (siapa memanggil siapa)

```text
app.py
  -> parsers/__init__ (parse_docling, parse_mineru)
  -> comparison (compare_results, similarity_matrix) + comparison.metrics.usage_stage_reports
  -> services.usage_tracker.combine_usage_reports
  -> utils (format support, nama file)
  -> config.AppConfig

parsers/docling_parser.py
  -> services.langextract_service.extract_claim_from_markdown
  -> services.llm_service.extract_with_remote_vlm        (legacy)
  -> schemas.native.NATIVE_SCHEMA_REGISTRY               (legacy)
  -> services.page_classifier.classify_pages
  -> services.claim_form.build_claim_form / build_full_claim_json
  -> services.usage_tracker (+ _vlm_usage lokal)
  -> config.AppConfig

services/langextract_service.py
  -> services.claim_fields (FIELD_SPECS, helper)
  -> services.claim_rules (kanonikalisasi, marker)
  -> schemas.claim_rulebook.DEFAULT_RULEBOOK
  -> schemas.claim_pipeline (kontrak)
  -> services.usage_tracker (UsageLedger + proxy perekam)

services/claim_rules.py     -> schemas.claim_rulebook (lazy import)
services/claim_form.py      -> services.claim_rules
services/page_classifier.py -> services.claim_rules.document_title (import di dalam fungsi)
```

Kopling melingkar diatasi secara eksplisit: `claim_rules._rulebook()` dan
`page_classifier.classify_pages` memakai **import di dalam fungsi**, bukan di
level modul.

## 9. Maintainability Evaluation

Penilaian di bawah ini adalah pembacaan kondisi kode saat ini, bukan usulan
perubahan.

### 9.1 Yang sudah kuat

| Aspek | Bukti di kode |
|-------|----------------|
| Aturan bisnis punya satu pintu | `ClaimRulebook` + `rulebook_prompt_section()`; prompt, schema, validasi, dan UI turun dari model yang sama |
| Deskripsi field menjadi kontrak, bukan prosa prompt | `Field(description=...)` di `PageClassification` dan rulebook; deskripsi ikut masuk ke JSON Schema |
| Disiplin anti-mengarang dijaga eksplisit | `_NON_ANSWERS` (`page_classifier.py:53`), `source="page_title"` (`claim_form.py:133`) / `"not_named"` (`page_classifier.py:283,288`), `status="default"`/`"derived"`/`"unknown"`; `_reason_of_visit` menolak menulis narasi bila fakta kurang |
| Kegagalan berisik, bukan diam | `langextract_fallback_to_legacy=False` (default), `LangExtractConfigurationError`, `validate_claim_contract` |
| Usage tidak dibulatkan menjadi tebakan | `reported` vs `estimated` dipisah; `source` selalu ditampilkan; `unavailable_usage_report` untuk stage yang tidak dieksekusi |
| Registry field punya self-check | `claim_fields.validate_registry()` + `described_fields()`/`undescribed_fields()` |
| Struktur modul jelas per tanggung jawab | 14 lapisan; hanya 3 dari ~20 file yang melewati 600 baris (daftar lengkap di tabel 12.1) |
| Ada test suite untuk jalur claim | `tests/test_claim_form.py`, `tests/test_langextract_service.py`, `tests/test_usage_tracker.py` |

### 9.2 Titik yang menambah biaya perubahan

| # | Titik | Bukti |
|---|-------|-------|
| M1 | Tiga modul besar menampung banyak tanggung jawab | `langextract_service.py` 2009 baris, `app.py` 1343, `llm_service.py` 834 |
| M2 | Dua sistem skema berdampingan (legacy vs LangExtract) | `schemas/native.py` dan `build_extraction_schema()` hidup bersamaan untuk tujuan mirip |
| M3 | Dua payload claim berdampingan | `build_claim_form` (`claim-form-v1`) + `build_grouped_claim_form` (`claim-full-v2`) dirender dua kali di tab JSON |
| M4 | Arti `.full.json` berbeda antar parser | bagian 2.1 / keputusan D34 |
| M5 | Nama field ganda: `snake_case` internal vs `camelCase` `editForm` | `FORM_NAME_ALIASES` + pemetaan manual per baris di `build_grouped_claim_form` |
| M6 | Aturan tersebar di 7 kanal (K1–K7) | bagian 4; konstanta (K7) dan prompt (K3) dapat menyimpang dari rulebook (K1) |
| M7 | Cache rulebook perlu `refresh_rule_cache()` manual | `claim_rules.py:48` |
| M8 | `build_full_claim_json` thin wrapper tanpa transformasi | `claim_form.py:776` — hasil identik `build_grouped_claim_form`, tetapi namanya menjanjikan payload berbeda |
| M9 | Import di dalam fungsi untuk memecah circular import | `claim_rules._rulebook`, `page_classifier.classify_pages`, `usage_tracker.build_usage_report` |
| M10 | `page_classifier` melakukan panggilan model sendiri | model call ke-2 di luar LangExtract; muncul di usage tree dengan stage berbeda |
| M11 | `pdf_inspector` diimpor tetapi tidak terdaftar di `requirements.txt` | `app.py:12` |
| M12 | Sebagian konstanta ranah berada di Python, bukan rulebook | `possibleNewClaim` (D7), `_RECORD_KEYS` (D26), marker default di `claim_rules.py` |

### 9.3 Ringkasan sifat arsitektur

Arsitektur ini bertipe **pipeline linear dengan satu titik aturan
(`ClaimRulebook`) yang di-fan-out ke empat konsumen** (prompt, JSON Schema,
fungsi Python, UI). Kekuatan utamanya adalah disiplin "tidak mengarang nilai"
dan pemisahan `reported` vs `estimated` pada usage. Biaya perubahannya
terkonsentrasi pada tiga file besar dan pada empat tempat di mana aturan,
skema, atau payload terduplikasi (M2–M5).

Skor ringkas (penilaian kualitatif, bukan angka terukur):

| Dimensi | Kondisi saat ini | Alasan |
|---------|------------------|--------|
| Menambah aturan bisnis | Sedang | Jalur resmi ada (`ClaimRulebook` + `Field(description=...)`), tetapi K3/K7 masih bisa menampung aturan paralel |
| Menambah field baru | Sedang | Harus menyentuh `claim_fields.FIELD_SPECS`, prompt (`_field_guide`), skema, lalu proyeksi form + pemetaan `editForm` |
| Mengganti parser | Rendah–sedang | `parse_*` punya kontrak hasil yang sama, tetapi tiap parser menyusun `.full.json` dan `raw_ocr_pages` sendiri (D34) |
| Menelusuri satu nilai | Baik | `provenance`, `evidence`, `status`, dan `method` tersimpan di kontrak dan grouped JSON |
| Menelusuri biaya | Baik | `usage` per stage + `source` + `reported_raw`, dan `usage_stage_reports()` untuk tabel benchmark |

## 10. Pain Points

Diurutkan dari yang paling sering menghambat perubahan.

### 10.1 Duplikasi arti, bukan duplikasi kode

| # | Masalah | Dampak nyata |
|---|---------|--------------|
| P1 | `.full.json` berarti contract penuh di Docling dan grouped slim di MinerU | Siapa pun yang membaca artefak harus tahu parser mana yang menghasilkannya |
| P2 | Dua payload claim (`claim-form-v1`, `claim-full-v2`) merender data yang sama | Perubahan satu field sering harus diubah di dua tempat |
| P3 | Nama field `snake_case` vs `camelCase` dipetakan manual baris per baris | Menambah field form = menambah baris di `build_grouped_claim_form` + `FORM_NAME_ALIASES` |
| P4 | Dua sistem skema (legacy `schemas/native.py` vs `build_extraction_schema`) | Dokumentasi dan kontrak ganda; sulit menjawab "skema resmi yang mana" |

### 10.2 Aturan yang bisa menyimpang

| # | Masalah | Dampak nyata |
|---|---------|--------------|
| P5 | Aturan tersebar di 7 kanal (K1–K7) | Menambah aturan di prompt (K3) tanpa menambah di rulebook (K1) membuat prompt dan validasi tidak sinkron |
| P6 | `possibleNewClaim` dan beberapa konstanta ranah ada di Python, bukan rulebook | Aturan tidak dapat diubah tanpa mengubah kode |
| P7 | Cache rulebook butuh `refresh_rule_cache()` | Perubahan rulebook saat runtime tidak otomatis terpakai |
| P8 | Marker default (`INPATIENT_TEXT_MARKERS`/`OUTPATIENT_TEXT_MARKERS`) berada di `claim_rules.py` | Ada dua sumber marker: konstanta default dan `rulebook.visits`/`rulebook.documents` |

### 10.3 Observabilitas dan ketahanan

| # | Masalah | Dampak nyata |
|---|---------|--------------|
| P9 | `page_classifier` melakukan panggilan model terpisah di luar LangExtract | Dua panggilan model per dokumen; usage-nya perlu diperlakukan berbeda di laporan |
| P10 | Stage usage `icd_suggestion` ditempel manual di luar daftar bersama `USAGE_STAGES = ("vlm_ocr", "extraction")` | `comparison/metrics.py:34-36` harus mengambil `usage["icd_suggestion"]` secara khusus, sehingga stage baru tidak otomatis ikut |
| P11 | `pdf_inspector` tidak terdaftar di `requirements.txt` | Instalasi bersih dari requirements bisa gagal pada import `app.py` |
| P12 | OCR Docling Remote VLM tidak mengekspos prompt sebagai string | `debug_prompts.ocr.prompt` hanya berisi penjelasan bahwa prompt dibuat internal Docling |

### 10.4 Ukuran file dan tata kelola

| # | Masalah | Dampak nyata |
|---|---------|--------------|
| P13 | `langextract_service.py` 2009 baris menampung prompt, skema, examples, serialisasi, normalisasi, analisis, validasi, ICD, dan perekaman usage | Setiap perubahan kecil menyentuh file yang sama; review lebih berat |
| P14 | `app.py` 1343 baris menampung render + orkestrasi + IO | Perubahan UI dan perubahan alur parser berada di file yang sama |
| P15 | Tidak ada `pyproject.toml`; hanya `requirements.txt` | Tidak ada metadata paket/entry point standar; tooling lint/test tidak dikonfigurasi terpusat |

## 11. Refactor Opportunities (kandidat — belum ada keputusan)

Bagian ini baru boleh dibaca sebagai **daftar kandidat**, bukan rencana yang
disetujui. Setiap kandidat dipetakan ke pain point/bukti yang sudah didokumentasi
di bagian 9 dan 10.

### 11.1 Prioritas tinggi (risiko rendah, dampak jelas)

| # | Kandidat | Menyelesaikan | Bukti pendukung | Risiko |
|---|----------|---------------|-----------------|--------|
| R1 | Samakan arti `{stem}.full.json` di semua parser (pilih satu: contract penuh atau grouped slim) | P1, D34 | `docling_parser.py:432` vs `mineru_parser.py:186-196` | Rendah — hanya isi artefak, tidak mengubah hasil render |
| R2 | Jadikan `build_full_claim_json` benar-benar berbeda, atau hapus dan pakai `build_grouped_claim_form` langsung | M8 | `claim_form.py:776` mendelegasi tanpa transformasi | Rendah — perlu pengecekan pemanggil |
| R3 | Daftarkan `pdf_inspector` di `requirements.txt` (atau jadikan import opsional dengan pesan jelas) | P11, M11 | `app.py:12` | Rendah |
| R4 | Pindahkan `possibleNewClaim` dan konstanta ranah lain ke `ClaimRulebook` | P6, M12 | D7, D26 | Sedang — menyentuh rulebook + derive + test |
| R5 | Satukan sumber marker RI/RJ (hapus konstanta default atau jadikan fallback eksplisit yang didokumentasikan) | P8 | `claim_rules.py::_text_markers_in` | Sedang — mengubah perilaku bila rulebook kosong |

### 11.2 Prioritas menengah (butuh keputusan desain)

| # | Kandidat | Menyelesaikan | Bukti pendukung | Risiko |
|---|----------|---------------|-----------------|--------|
| R6 | Tetapkan satu payload claim kanonik; jadikan payload kedua sebagai adapter turunan (bukan pembangun paralel) | P2, M3 | `build_claim_form` + `build_grouped_claim_form` | Sedang–tinggi — menyentuh UI dan test |
| R7 | Ganti pemetaan `editForm` baris-per-baris dengan tabel pemetaan deklaratif (`FieldSpec` → `editForm`) | P3, M5 | `FORM_NAME_ALIASES`, `build_grouped_claim_form` | Sedang — perubahan mekanis, bisa diverifikasi test |
| R8 | Pisahkan `langextract_service.py` menjadi modul per tanggung jawab (prompt, schema, examples, serialize, analyze, validate, icd, usage) | P13, M1 | 2009 baris, 40+ fungsi | Tinggi — banyak pemanggil dan test |
| R9 | Kurangi kanal aturan: arahkan K3/K7 agar seluruhnya membaca K1 | P5, M6 | `build_prompt` item 14, konstanta di `claim_rules.py` | Sedang–tinggi — mengubah prompt berarti mengubah output model |
| R10 | Buat cache rulebook otomatis (hash/timestamp) alih-alih `refresh_rule_cache()` manual | P7, M7 | `claim_rules.py:48` | Rendah–sedang |
| R11 | Seragamkan penanganan stage usage (`icd_suggestion`, klasifikasi halaman) dalam satu daftar `USAGE_STAGES` + `USAGE_STAGE_LABELS` | P10 | `comparison/metrics.py:9,34` + `app.py:41` | Rendah |

### 11.3 Prioritas rendah / strategis

| # | Kandidat | Menyelesaikan | Bukti pendukung | Risiko |
|---|----------|---------------|-----------------|--------|
| R12 | Putuskan masa depan `schemas/native.py` (legacy) — dipertahankan sebagai jalur non-claim atau diarsipkan | P4, M2 | `NATIVE_SCHEMA_REGISTRY` masih dipakai `docling_parser.py:359` | Sedang |
| R13 | Pisahkan `app.py` menjadi modul render + modul orkestrasi | P14, M1 | 1343 baris | Sedang — UI tanpa test otomatis |
| R14 | Tambahkan `pyproject.toml` + konfigurasi test/lint terpusat | P15 | `requirements.txt` saja | Rendah |
| R15 | Tambahkan test untuk jalur MinerU/opendataloader/marker (belum ada di `tests/`) | bagian 9.1 | hanya 3 modul test untuk jalur Docling/claim | Sedang |

### 11.4 Yang **tidak** disarankan untuk diubah

| Area | Alasan |
|------|--------|
| Disiplin anti-mengarang (`_NON_ANSWERS`, `source`, `status`) | Ini pembeda nilai utama arsitektur; melemahkannya membuat JSON terdengar lengkap tapi tidak dapat diaudit |
| Pemisahan `reported` vs `estimated` pada usage | Menggabungkannya membuat angka biaya menjadi tebakan yang tak terlihat |
| `langextract_fallback_to_legacy = False` sebagai default | Fallback diam-diam menyembunyikan kegagalan ekstraksi |
| `rulebook_prompt_section()` sebagai turunan rulebook | Ini mekanisme "1 pintu"; mengganti dengan prompt manual mengembalikan masalah P5 |

## 12. Cara Verifikasi Dokumen Ini

Semua klaim di atas dapat diverifikasi dengan perintah berikut (dijalankan dari
root repo, PowerShell). Semua perintah di bawah **sudah dijalankan saat
dokumen ini ditulis**, beserta hasilnya.

```powershell
# 1) Test suite jalur claim  ->  "Ran 94 tests ... OK"
.\.venv\Scripts\python -m unittest tests.test_claim_form tests.test_langextract_service tests.test_usage_tracker

# 2) Konsistensi registry field -> "registry issues: []" (kosong = sehat)
.\.venv\Scripts\python -c "from services.claim_fields import validate_registry; print('registry issues:', validate_registry())"

# 3) Rulebook valid + blok yang benar-benar ada (harus 7 kunci)
.\.venv\Scripts\python -c "from schemas.claim_rulebook import validate_rulebook, DEFAULT_RULEBOOK; print(sorted(validate_rulebook(DEFAULT_RULEBOOK.model_dump()).model_dump().keys()))"

# 4) Prompt + skema yang benar-benar dikirim (tanpa memanggil API)
#    -> "prompt chars: 5771" dan top-level schema properties == ['extractions']
.\.venv\Scripts\python -c "from services.langextract_service import build_prompt, build_extraction_schema; print('prompt chars:', len(build_prompt())); print(list(build_extraction_schema().get('properties', {})))"

# 5) `build_full_claim_json` adalah thin wrapper, bukan objek fungsi yang sama
#    -> False (hasil identik, identitas fungsi berbeda)
.\.venv\Scripts\python -c "from services.claim_form import build_full_claim_json, build_grouped_claim_form; print(build_full_claim_json is build_grouped_claim_form)"
```

Catatan penting: `python -m unittest discover -s tests -t .` **gagal** di repo
ini (`ImportError: Start directory is not importable`), karena `tests/` tidak
punya `__init__.py`. Gunakan selalu bentuk `unittest tests.<modul>` seperti
perintah (1).

### 12.1 Hasil verifikasi saat dokumen ini ditulis

| Cek | Hasil |
|-----|-------|
| Test suite `test_claim_form` + `test_langextract_service` + `test_usage_tracker` | `Ran 94 tests in 5.045s` — `OK` |
| `claim_fields.validate_registry()` | `[]` (tanpa masalah) |
| `validate_rulebook(DEFAULT_RULEBOOK.model_dump())` | Berhasil, 7 blok: `claim_types`, `confidence_tiers`, `documents`, `name_check`, `overseas`, `period`, `visit` |
| `len(build_prompt())` | 5771 karakter |
| Top-level `build_extraction_schema()` | `type`, `properties` (`extractions`), `required`, `additionalProperties` |
| `build_full_claim_json is build_grouped_claim_form` | `False` (thin wrapper) |
| Jumlah baris file (dihitung ulang, semua angka cocok) | `langextract_service.py` 2009, `app.py` 1343, `llm_service.py` 834, `claim_form.py` 798, `usage_tracker.py` 621, `claim_fields.py` 508, `docling_parser.py` 476, `mineru_parser.py` 332, `page_classifier.py` 302, `claim_rules.py` 292, `claim_rulebook.py` 264, `config.py` 255, `native.py` 206, `opendataloader_parser.py` 194, `marker_parser.py` 95, `metrics.py` 69, `claim_pipeline.py` 64 |

### 12.2 Hal yang belum diverifikasi penuh di dokumen ini

| Item | Status |
|------|--------|
| Jumlah baris tiap file | Dihitung ulang lewat skrip pada sesi ini (lihat tabel 12.1) |
| Isi lengkap `services/claim_fields.py::FIELD_SPECS` (semua entri) | Baru sebagian; daftar field lengkap perlu dibaca penuh bila akan menambah field |
| Isi lengkap `services/llm_service.py` (834 baris) | Dibaca sebagian; cukup untuk peran lapisan, belum untuk tabel keputusan per fungsi legacy |
| Detail `parsers/marker_parser.py` dan `opendataloader_parser.py` | Belum dibaca pada sesi ini; perannya di tabel lapisan L2 masih ringkas |
| Angka biaya nyata (USD/IDR) | Tidak pernah dijalankan dengan kredensial API di sesi ini; hanya jalur perhitungan yang dibaca |
| Format asli `{stem}.json` dari run nyata | Tidak ada artefak hasil run di repo saat sesi ini; isi diambil dari kode penulis artefak |

## 13. Status Dokumen

| Bagian | Status |
|--------|--------|
| 1–8 (End-to-End Flow, arsitektur L1–L14, Runtime Flow, Rule Flow K1–K7, Prompt Flow, Schema Flow, Decision Table D1–D36, Dependencies) | **Selesai** |
| 9 (Maintainability Evaluation: 9.1 kuat / 9.2 M1–M12 / 9.3 ringkasan) | **Selesai** |
| 10 (Pain Points P1–P15) | **Selesai** |
| 11 (Refactor Opportunities R1–R15) | **Daftar kandidat — belum ada keputusan.** Prioritas akhir menunggu arahan pemilik produk |
| 12 (Cara verifikasi + hasil verifikasi) | **Selesai**, semua perintah di bagian 12 sudah dijalankan |

Catatan kepatuhan terhadap permintaan awal: bagian 11 sengaja ditulis sebagai
daftar kandidat yang dipetakan ke pain point, tanpa satu pun perubahan kode.
Tidak ada file program yang diubah selama penyusunan laporan ini — hanya
`prd.md`.

### 13.1 Koreksi yang sudah dilakukan setelah verifikasi

Verifikasi ulang menemukan beberapa angka/nama yang salah dan sudah diperbaiki
di dokumen ini:

| Sebelum | Sesudah | Bukti |
|---------|---------|-------|
| `FORM_NAME_ALIASES` (56) | (43) | `services/claim_form.py:43` |
| `FIELD_SPECS` (58) | (51) | `services/claim_fields.py:51` |
| `rulebook.visits`, `rulebook.periods`, `rulebook.claim_types[].aliases`, `rulebook.overseas.countries`, `rulebook.name_check.similarity_threshold_pct`, `documents.inpatient_docs` | `rulebook.visit`, `rulebook.period`, `.labels`/`.needs_formulir`, `overseas.country_hints`, `name_check.threshold_pct`, `documents.inpatient_document_types` | `schemas/claim_rulebook.py` + output `model_dump()` |
| "`build_full_claim_json` hanya alias" | "thin wrapper tanpa transformasi (`is` → `False`)" | `inspect.getsource` + perbandingan identitas |
| `source="not_named"` disebut ada di `claim_form.py` | `"page_title"` di `claim_form.py:133`; `"not_named"` di `page_classifier.py:283,288` | pembacaan kedua file |
| `DocumentCategory` disebut "class" | Alias `Literal[...]` (7 nilai) + tuple `DOCUMENT_CATEGORIES` | `services/page_classifier.py:31,41` |
| Ringkasan awal menulis `*.full.json` = grouped slim untuk semua parser | Docling: contract penuh (`docling_parser.py:432`); MinerU: grouped slim (`mineru_parser.py:186`) | pembacaan penulis artefak |
| `source="not_named"` di `claim_form.py` | `"page_title"` di `claim_form.py:133`, `"not_named"` di `page_classifier.py:283,288` | pembacaan file |
