# Rulebook: 1 pintu rules + schema + prompting

Semua rules klaim tinggal di **satu file Pydantic**:

`schemas/claim_rulebook.py` → `ClaimRulebook`

Prinsip: **tambah rules = tambah 1 field + 1 kalimat `description`.**
Prompt text, JSON schema ke LLM, validasi, dan UI ikut berubah otomatis.
Jangan edit string prompt / tuple rules manual di file lain.

## 1. Struktur

- `claim_types`: kategori `new | completeness | review` + label eksplisit.
  `new` butuh halaman `formulir_klaim`.
- `visit`: label RI/RJ + aturan periode
  (`inpatient_must_be_in_stay`, `outpatient_receipt_can_be_stay`,
  `resume_is_primary_for_ri`).
- `documents`: kategori dokumen → visit type
  (`resume/discharge_letter` = RI, `invoice` = RJ) + frase fallback
  (`inpatient_text_markers`, `outpatient_text_markers`).
- `overseas`: `country_hints` + `country_currency` fallback
  (Singapore→SGD, Malaysia→MYR, Korea→KRW) + `abroad_exception`.
  Currency eksplisit dokumen selalu menang.
- `period`: `max_days_after_discharge` (kwitansi di luar periode
  jadi kandidat `possibleNewClaim`).
- `name_check`: `mandatory` + `threshold_pct` (default 85).
- `confidence_tiers`: `explicit_label=100`,
  `admission+discharge+resume=92`, `resume_only=80`,
  `single_receipt_same_date=65`, `mixed_or_insufficient=None`.
  Skala 0–100, heuristik non-kalibrasi.
- Hasil: `ClaimSignals` (`isNewClaim/isKelengkapan/isRI/isRJ/
  isOverseas/possibleNewClaim/riConfidence/rjConfidence`)
  + `Decision{value,reason,evidence,method,confidence}`.

## 2. Cara nambah rules (contoh)

```python
class OverseasRule(BaseModel):
    country_currency: dict[str, str] = Field(
        default={"Singapore": "SGD", "Korea": "KRW", "Vietnam": "VND"},
        description="Fallback currency dari lokasi provider.",
    )
```

Simpan → prompt (`build_prompt()`), schema
(`build_extraction_schema()`), validasi
(`ClaimRulebook.model_validate`), dan tab Debug prompt ikut update.
Pydantic menolak value invalid (`confidence=150` ditolak `le=100`).

## 3. Cara pakai custom rulebook di kode

```python
from schemas.claim_rulebook import ClaimRulebook
from services.langextract_service import build_prompt
from services.claim_rules import canonical_visit_type

custom = ClaimRulebook()
custom.visit.inpatient_labels = ["opname"]
prompt = build_prompt(custom)  # Business rules ikut berubah
canonical_visit_type("opname", custom)  # "inpatient"
```

Fungsi rules (`canonical_visit_type`, `canonical_claim_case_type`,
`visit_type_from_documents`, `detect_overseas`,
`overseas_currency_fallback`, `name_similarity_pct`) menerima
argumen opsional `rulebook`; tanpa argumen memakai
`DEFAULT_RULEBOOK` agar kode lama tetap jalan.

## 4. Output JSON

- `*.json` → `claim_form` (lama) + `grouped_claim_form`
  (slim per screenshot: `memberInfo/header/policyInfo/serviceLines`
  + `decisions` + `usage`).
- `*.full.json` → slim grouped (bukan kontrak verbose lama).
- Parent kosong total dihapus (`_prune_parent`); key memakai `name`
  editForm (`insuredName`, `visitFrmDtm`, `hphpName`, `hpspNames[]`,
  `incuredAmt`, `reasonOfVists`) agar autofill `$('#name')` 1:1.
- `decisions` wajib ada `reason` agar analyst/developer tau kenapa
  RI/RJ/overseas/new/kelengkapan dipilih.
- `usage` LLM (`vlm_ocr/extraction/total`, reported vs estimated
  tidak dicampur) ikut di full JSON.

## 5. Test

```powershell
.\\.venv\\Scripts\\python -m unittest tests.test_claim_form tests.test_langextract_service tests.test_usage_tracker
```

Test kunci: `GroupedSlimTests` (parent screenshot + prune +
multi-doctor + decisions + usage), `RulebookSingleDoorTests`
(prompt dari rulebook + validasi + custom rulebook).
