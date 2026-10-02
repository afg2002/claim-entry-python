# Panduan developer: menambah field claim LangExtract

Gunakan dokumen ini sebagai checklist ketika menambah field di pipeline:

```text
PDF -> VLM/OCR -> Markdown -> LangExtract -> Python merge/normalization -> JSON
```

## Aturan utama

- LangExtract hanya mengambil fakta yang tertulis eksplisit.
- `claim_data` menyimpan nilai mentah; tanggal/angka hasil parsing masuk `normalized_claim_data`.
- Nilai yang sama pada span berbeda tetap occurrence berbeda.
- Fact harus memiliki provenance (`char_start`, `char_end`, `source_text`, `extraction_id`).
- Inferred value hanya masuk `analyst_analysis` dengan `status=derived` atau `unknown`.
- Jangan mengirim Markdown terfilter ke LangExtract; kirim seluruh Markdown hasil VLM.

## Menambah field facts

### 1. Allowlist LLM

Edit `services/langextract_service.py`, tuple `CLAIM_FIELDS`:

```python
CLAIM_FIELDS = (
    # existing fields
    "provider_tax_id",
)
```

Prompt dan `output_schema` membaca tuple ini secara otomatis.

### 2. Tentukan tipe

- Satu nilai: tambahkan ke `SCALAR_FIELDS`.
- Banyak nilai: tambahkan ke `LIST_FIELDS`.
- Jangan menambah derived field ke allowlist facts.

Builder claim juga perlu memiliki default/output yang sesuai di `_empty_claim_data` dan `build_claim_contract` bila nama list berbeda.

### 3. Normalisasi

Di `_build_normalized_claim_data`, tentukan apakah field adalah:

- string biasa: copy tanpa perubahan;
- tanggal: masukkan `DATE_FIELDS`;
- nominal: masukkan `NUMERIC_FIELDS`;
- currency: gunakan normalizer symbol/code yang tersedia.

Jangan mengganti raw value di `claim_data`. Contoh:

```python
claim_data["total_amount"] = "Rp 986.882"
normalized_claim_data["total_amount"] = 986882
normalized_claim_data["currency"] = "IDR"  # derived dari marker amount
```

Jika currency tidak berdiri sendiri di dokumen, `claim_data.currency` tetap `null`; hasil inference harus berada di `analyst_analysis.currency` dengan evidence dan `status="derived"`.

### 4. Relationship/group

Jika field punya attribute, edit:

1. `FAMILY_BY_FIELD` untuk family default;
2. `_grouped_records(...)` di `build_claim_contract`;
3. few-shot di `build_examples()`;
4. output `*_records` di `claim_data`.

Contoh medication:

```python
medication_records = _grouped_records(
    grounded_items,
    family="medication",
    fields=("medication", "dosage", "frequency", "duration"),
    anchor_field="medication",
)
```

`group_id` dari LLM hanya hint; Python membuat canonical occurrence ID dan tidak menghapus occurrence yang berbeda posisinya.

### 5. Schema final

Edit `schemas/claim_pipeline.py` bila field menjadi bagian API publik. Tambahkan tipe, nullability, format/pola, dan relationship. Jangan memasukkan enum domain bisnis sebagai fakta dokumen; enum dapat berada di validation/analyst layer.

### 6. Test dan dokumentasi

Edit `tests/test_langextract_service.py` dan README. Minimal test:

- raw value dan leading zero tetap utuh;
- provenance memiliki offset dan source excerpt;
- field absent menjadi `null`/`[]`;
- duplicate text pada span berbeda tidak dihapus;
- ungrounded item tidak masuk facts;
- derived field tidak masuk `claim_data`.

Jalankan:

```powershell
python -m unittest tests.test_langextract_service -v
```

Naikkan `SCHEMA_VERSION` bila mengubah bentuk atau makna field.

## Menambah rules bisnis (1 pintu: ClaimRulebook)

Jangan edit tuple di `services/claim_rules.py` atau string prompt di
`services/langextract_service.py`. Edit `schemas/claim_rulebook.py`:

1. Tambah/ubah 1 field Pydantic + 1 kalimat `Field(description=...)`.
2. Prompt (`build_prompt(rulebook)`), schema, validasi, dan tab Debug
   prompt ikut berubah otomatis.
3. Fungsi rules (`canonical_visit_type`, `canonical_claim_case_type`,
   `visit_type_from_documents`, `detect_overseas`,
   `overseas_currency_fallback`, `name_similarity_pct`) menerima
   argumen opsional `rulebook`; default `DEFAULT_RULEBOOK`.

Detail lengkap: [`docs/claim_rulebook.md`](claim_rulebook.md).

## Menambah field derived

Jangan menambah `risk_score`, `is_fraud`, `recommended_visit_type`, atau `suggested_icd_code` ke `CLAIM_FIELDS`. Tambahkan di `derive_claim_analysis()`:

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

Confidence LLM harus ditandai non-calibrated. ICD suggestion harus melalui pass terpisah dan hanya disimpan di `analyst_analysis.icd_suggestions`, tidak pernah ke `document_icd_code`/`icd_codes`.

## Menambah tahap berbiaya baru

Bila menambah panggilan model baru, catat token usage-nya lewat `services/usage_tracker.py`:

```python
from services.usage_tracker import UsageLedger, build_usage_report

ledger = UsageLedger()
ledger.record(response.usage)          # angka dari provider
usage = build_usage_report(
    ledger,
    stage="tahap_baru",                # label tahap
    model=model_name,
    provider="openai",
    usd_to_idr=config.usd_to_idr,
    input_rate_per_million=input_rate,
    output_rate_per_million=output_rate,
)
```

Aturan yang harus dijaga:

- Jangan pernah mengarang angka. Bila provider tidak mengirim `usage`, `normalize_usage` mengembalikan `None` dan tahap boleh turun ke estimasi lewat `ledger.estimate(...)`, dikendalikan `config.usage_estimate_fallback`.
- Angka `reported` dan `estimated` tidak boleh dicampur dalam satu field. Field headline memakai angka provider bila ada; estimasi tetap disimpan terpisah di `estimated`.
- Tambahkan key tahap ke `USAGE_STAGE_LABELS` di `app.py` dan ke `USAGE_STAGES` di `comparison/metrics.py` agar muncul di tab Usage & Biaya dan tabel benchmark.
- Bila tahap memakai concurrency, gunakan `UsageLedger` (aman terhadap thread), bukan penjumlahan manual.

## Deskripsi field

Setiap field yang namanya tidak self-explanatory diberi `description` di `FIELD_SPECS`. Deskripsi ini dikirim ke model dalam dua tempat:

- `build_prompt()` merakit katalog `nama : arti` untuk semua field ber-deskripsi;
- `build_extraction_schema()` menambahkan JSON-Schema `description` pada atribut `field_name`, namun hanya untuk daftar field yang paling mudah tertukar (`_SCHEMA_DESCRIPTION_FIELDS`).

Deskripsi tidak diulang penuh di kedua tempat: prompt sudah memuat katalognya, sehingga menyalin semuanya ke schema akan menggandakan biaya tetap tiap request tanpa menambah sinyal.

Aturan saat menambah field:

- Field yang jelas (`address`, `diagnosis`, `policy_holder_name`) sengaja dibiarkan tanpa deskripsi untuk menghemat token.
- Description **hanya boleh** pada field `scalar`/`list`. Field `derived`/`group` tidak boleh, karena itu akan mendorong model menghasilkannya; `validate_registry()` menolak pelanggaran ini.
- Nama dokumen (`document_type`) **dipakai apa adanya** dari LLM dan tidak pernah dipaksa ke daftar. Tidak ada label tangkap-all seperti `other`; halaman yang tidak dinamai LLM tetap `null` dengan `source="not_named"` agar celahnya terlihat, bukan disamarkan.
- Kategori bisnis ditambahkan sebagai enum **terpisah** (`DOCUMENT_CATEGORIES` / `DocumentCategory`) tanpa nilai tangkap-all, dan hanya dipakai `visit_type_from_documents` untuk menentukan rawat inap atau rawat jalan. Tambah label baru di `schemas/claim_rulebook.py` (`DocumentRules.inpatient_text_markers` / `outpatient_text_markers` / `inpatient_document_types`), bukan di tuple `services/claim_rules.py`.
- Kontrak respons `PageClassification` di `services/page_classifier.py` adalah Pydantic model. Semua arahan ada di `Field(description=...)`, yang otomatis ikut ke JSON schema, sehingga prompt cukup satu kalimat.
- `document_type` sengaja **tidak** memakai enum: memaksanya ke daftar tertutup hanya menghasilkan label generik untuk dokumen di luar daftar.

## Review checklist

- [ ] allowlist facts dan scalar/list diperbarui;
- [ ] family/group/few-shot diperbarui bila relasi;
- [ ] raw versus normalized didokumentasikan;
- [ ] provenance dan test ditambahkan;
- [ ] schema final dan README diperbarui;
- [ ] tidak ada inferred value di `claim_data`;
- [ ] usage tahap baru dicatat lewat `UsageLedger` dan berlabel sumbernya;
- [ ] unit test lulus.