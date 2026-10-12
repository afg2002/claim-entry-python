# Document Parser Comparison Lab v2

## Windows setup

```powershell
cd document-parser-comparison-streamlit-v2
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip uv
uv pip install -r requirements.txt
java -version
mineru --help
python -m streamlit run app.py
```

Gunakan Marker mode `fast` untuk CPU Windows. Mode `balanced` membutuhkan resource inference yang lebih berat. Marker menghasilkan Markdown dan JSON terstruktur untuk PDF. Gunakan MinerU backend `pipeline` untuk CPU Windows. Aplikasi menyimpan upload sebagai nama internal pendek agar menghindari trailing-space dan MAX_PATH pada Windows. Live log MinerU dibaca melalui `subprocess.Popen`. Progress halaman hanya presisi jika versi MinerU mencetak `page x/y`; jika tidak, UI menampilkan elapsed time, tahap, dan indikator aktivitas.

## Pipeline claim dengan LangExtract

Default claim flow pada aplikasi ini adalah:

```text
PDF
  -> Docling Remote VLM (hanya OCR/layout/table -> Markdown)
  -> LangExtract (Markdown penuh -> grounded extraction items)
  -> Python grouping/normalization/validation
  -> analyst_analysis
  -> final structured JSON
```

LangExtract tidak menerima gambar halaman dan tidak menggantikan tahap parser. `services/langextract_service.py` mengirim seluruh Markdown tanpa keyword filtering. Setiap item menyimpan `extraction_text`, `char_start`, `char_end`, source excerpt, page hint bila marker halaman tersedia, alignment status, dan status grounding. Item tanpa interval disimpan sebagai diagnostic dan tidak masuk `claim_data`.

### Pilih parser di Streamlit

Sidebar `Parser PDF/Markdown` mendukung:

- **Docling** — Remote VLM/API untuk tahap OCR/layout/table → Markdown, lalu LangExtract.
- **MinerU** — `mineru -b pipeline` lokal untuk OCR/layout/table → Markdown, lalu LangExtract.
- Keduanya dapat dipilih bersamaan untuk membandingkan Markdown dan waktu/hasil extraction.

### Upload beberapa dokumen

Pilih beberapa file sekaligus, lalu pilih mode pemrosesan:

- **Pisah per file** — setiap dokumen mendapat hasil Markdown dan extraction sendiri. File yang formatnya tidak didukung parser akan ditandai gagal tanpa menghentikan file lain.
- **Gabung Markdown saja** — tiap file tetap diekstrak sendiri; Markdown yang berhasil kemudian digabung per parser untuk preview/download. Jika ada file gagal, hasil gabungan diberi peringatan.
- **Gabung sebagai satu claim** — OCR dijalankan untuk semua file, Markdown digabung dengan nama sumber dan nomor halaman gabungan, lalu LangExtract dijalankan sekali per parser. Jika salah satu file gagal, claim gabungan parser tersebut dibatalkan.

Mode gabung hanya menawarkan parser yang mendukung semua file terpilih. Artifact tiap sumber dan artifact gabungan tetap tersedia di ZIP.

MinerU sengaja tidak memakai `mineru-api` dan tidak memakai VLM/API. Jika `LANGEXTRACT` gagal, proses MinerU ditandai error agar Markdown tidak diam-diam dianggap sudah terekstraksi; periksa credential/model LangExtract dan log.

### Konfigurasi

Salin `config.properties.example` menjadi `config.properties`, lalu isi API key/gateway. Parameter penting:

- `LANGEXTRACT_PROVIDER`: `openai`, `gemini`, atau `ollama`.
- `LANGEXTRACT_MODEL`: model extraction; model ini terpisah dari `VLM_MODEL`.
- `LANGEXTRACT_MAX_CHAR_BUFFER`: 1500–3000 adalah titik awal untuk klaim; nilai terlalu kecil memecah relasi, terlalu besar mengurangi konteks.
- `LANGEXTRACT_MAX_WORKERS`: mulaikan 2–4; naikkan hanya setelah rate limit dan biaya diukur.
- `LANGEXTRACT_BATCH_LENGTH`: minimal `max_workers`; adapter otomatis menaikkan nilai yang lebih kecil.
- `LANGEXTRACT_EXTRACTION_PASSES`: 1 untuk operasi normal, 2 untuk dokumen sulit dengan biaya lebih tinggi.
- `LANGEXTRACT_CONTEXT_WINDOW_CHARS`: konteks chunk sebelumnya, opsional.
- `LANGEXTRACT_USE_OUTPUT_SCHEMA`: `true` untuk OpenAI/Gemini. `output_schema` dibatasi oleh adapter/provider; Ollama menggunakan mode examples/prompt-only.
- `LANGEXTRACT_ENABLE_ICD_SUGGESTIONS`: default `false`. Jika diaktifkan, pass kedua yang terisolasi hanya menulis ke `analyst_analysis.icd_suggestions`; kode suggestion tidak pernah masuk `document_icd_code`.
- `LANGEXTRACT_FALLBACK_TO_LEGACY`: default `false` agar kegagalan tidak diam-diam kembali ke extractor giant-JSON lama.

### Token usage dan biaya

Setiap dokumen menyimpan usage per tahap di `output/<parser>/<nama>.json` pada key `usage`, dan UI menampilkannya di tab **Usage & Biaya**.

```json
"usage": {
  "vlm_ocr":     { "stage": "vlm_ocr",     "input_tokens": 0, "output_tokens": 0 },
  "extraction":  { "stage": "langextract", "input_tokens": 0, "output_tokens": 0 },
  "total":       { "stage": "document_total", "input_tokens": 0, "output_tokens": 0 }
}
```

Dua sumber angka selalu dipisahkan dan tidak pernah dicampur:

- `reported` — angka apa adanya dari blok `usage` provider.
- `estimated` — perkiraan `tiktoken` untuk bagian yang tidak dilaporkan provider.
- `source` — `provider`, `estimated`, `mixed`, atau `none`.

Angka `estimated` **bukan tagihan aktual** dan selalu ditandai demikian di UI. Halaman VLM berupa gambar, sehingga sisi input tidak dapat ditokenisasi lokal; hanya sisi output Markdown yang diestimasi dalam kasus itu.

Sumber data per tahap:

- **VLM/OCR** — Docling sudah mencatat payload `usage` gateway per halaman di `ConversionResult.pages[*].predictions.vlm_response.usage`, dan aplikasi menjumlahkannya.
- **LLM extraction** — LangExtract membuang payload `usage` provider, jadi `services/langextract_service.py` membungkus SDK client yang sudah dibuat adapter untuk merekam setiap respons. Hook ini berada di satu file dan aman: bila bentuk client berubah, tahap turun ke estimasi, tidak error.
- **OCR lokal / MinerU** — tidak memanggil model remote, sehingga dilaporkan `source=none` dengan alasannya, bukan baris nol yang terlihat seperti inference gratis.

Tarif per tahap (opsional, default ke `INPUT_RATE_PER_MILLION`/`OUTPUT_RATE_PER_MILLION`):

- `VLM_INPUT_RATE_PER_MILLION`, `VLM_OUTPUT_RATE_PER_MILLION`
- `LANGEXTRACT_INPUT_RATE_PER_MILLION`, `LANGEXTRACT_OUTPUT_RATE_PER_MILLION`
- `USAGE_ESTIMATE_FALLBACK` — `true` (default) memakai estimasi tiktoken bila provider diam; `false` menampilkan "tidak tersedia".

### Kontrak output

`claim_data` berisi fakta eksplisit saja. Nilai source tetap dipertahankan; tanggal/angka yang sudah diparse placed in `normalized_claim_data`. Relasi disimpan sebagai grouped records (`diagnosis_records`, `medication_records`, `invoice_records`, `lab_results`) dan provenance item tetap tersedia untuk penelusuran. `analyst_analysis` hanya berisi derived/analyst output dengan `status`, evidence, dan confidence type. `schemas/claim_pipeline.py` menyediakan validasi Pydantic dan JSON Schema untuk kontrak final.

### JSON ringkas (claim form)

JSON yang dibaca user bentuknya mengikuti formulir klaim, bukan dump kontrak:

```json
{
  "schema_version": "claim-form-v1",
  "document":       { "total_pages": 2, "pages": [ { "page": 1, "document_type": "Formulir Pengajuan Klaim", "category": "formulir_klaim", "label": "FORMULIR PENGAJUAN KLAIM" } ] },
  "member_info":    { "insured_name": "…", "dob": "…", "patient_id": "…", "mobile_phone": "…" },
  "policy_info":    { "policy_holder_name": "…", "policy_number": "…" },
  "claim":          { "claim_case_type": "new", "account_no": "…", "received_date": "…" },
  "visit":          { "type_of_visit": "outpatient", "status": "derived", "method": "outpatient_receipt_evidence" },
  "provider":       { "provider_name": "…", "doctor_names": ["…"] },
  "medical":        { "reason_of_visit": "…", "icd_codes": [], "icd_recommendations": [] },
  "financial":      { "amount_incurred": 1215882, "currency": "IDR", "exchange_rate": 1 },
  "service_lines":  [ { "no_of_days": 0, "incurred_amount": 1215882 } ],
  "medications": [], "procedures": [], "lab_results": [],
  "review":         { "needs_attention": ["…"] }
}
```

### Docling confidence scores

Jika versi dan pipeline Docling menyediakannya, report dari `ConversionResult.confidence` disimpan di dua tempat: `json_data.confidence` untuk akses cepat dan `json_data.docling_document.confidence` bersama raw document. Report mencakup `mean_grade`/`low_grade`, component scores (`layout_score`, `ocr_score`, `parse_score`, `table_score`), dan score per halaman. Tab **Raw OCR** menampilkan ringkasan serta tabel per halaman. Nilai yang tidak tersedia ditulis `null`, bukan dianggap score nol. Mengikuti dokumentasi Docling, gunakan grade sebagai ringkasan kualitas; score numerik bersifat informational dan dapat berubah.

Aturan yang dijaga:

- **Tidak ada angka karangan.** Nilai disalin dari `claim_data`/`normalized_claim_data`. `currency` dan `exchange_rate` memakai default formulir (IDR, 1) dan ditandai `currency_status`/`exchange_rate_status = "default"` agar tidak disalahbaca sebagai fakta dokumen.
- **Tipe kunjungan bisa dibaca dari dokumen pendukung.** Selain label eksplisit dan pasangan tanggal masuk/keluar, `resume`/`surat keluar` menandakan rawat inap dan `kwitansi` tunggal menandakan rawat jalan. Bundle campuran tetap `unknown` — sistem tidak menebak.
- **`reason_of_visit` disusun deterministik** dari fakta yang sudah ter-grounding, bukan hasil karangan model. Untuk rawat inap memakai periode inap; rawat jalan lebih ringkas. Bila datanya kurang, statusnya `unknown` dengan catatan, bukan kalimat yang terdengar lengkap tapi kosong.
- **Rekomendasi ICD tidak pernah masuk `icd_codes`.** Pass ICD berjalan otomatis hanya bila ada diagnosis dan dokumen tidak menulis kode ICD; hasilnya berstatus `derived` di `icd_recommendations` dan tetap perlu validasi coder.
- **Field kosong tetap ditulis `null`** supaya bentuk JSON stabil antar-run dan aman untuk diff.

Kontrak lengkap yang lama tidak hilang: ditulis ke `<nama>.full.json` dan tetap tersedia di tab JSON lewat `information_extraction`, sehingga audit dan regression tetap aman.

[`docs/langextract_claim_developer_guide.md`](docs/langextract_claim_developer_guide.md) adalah checklist developer untuk menambah field baru, relationship, normalisasi, provenance, test, dan dokumentasi.

### Batasan penting

- `output_schema` LangExtract adalah schema raw envelope `{"extractions": [...]}`, bukan schema bisnis Claim JSON. Ia tidak untuk final JSON.
- `char_interval` adalah 0-based half-open span dalam input Markdown. Page number tidak selalu dapat dipertahankan jika Docling tidak menyisipkan page marker; char offsets tetap sumber primer.
- Aturan/model ID dari provider dapat berubah. Python membuat canonical occurrence IDs dan tidak deduplikasi teks yang sama pada span berbeda.
- LangExtract bukan ICD ontology/medical coding validator. Suggestion harus melalui terminology server dan human review.
- Token usage dibaca dari blok `usage` provider bila ada; bila tidak, angka `estimated` (tiktoken) dipakai dan ditandai. Cost dihitung dari tarif per tahap dan merupakan estimasi, bukan tagihan yang telah diverifikasi.
- Gangguan `EOF while parsing JSON` diminimalkan dengan output schema + item extraction + resolver suppress parse errors, tetapi bukan jaminan semua provider comply.

### Menambah field

Ikuti checklist di [`docs/langextract_claim_developer_guide.md`](docs/langextract_claim_developer_guide.md). Dokument tersebut menyebutkan file dan urutan perubahan untuk facts, relationship, normalisasi, derived fields, provenance, dan test.

### Test offline

```powershell
python -m unittest discover tests -v
```

`tests/test_usage_tracker.py` tidak melakukan network/API call: ia menguji normalisasi payload usage OpenAI/Gemini, agregasi ledger (termasuk konkurensi), pemisahan angka `reported` vs `estimated`, pembacaan usage VLM per halaman dari objek konversi Docling, dan proxy pencatat usage pada SDK client. `tests/test_langextract_service.py` tetap menguji kontrak claim.

Test tidak melakukan network/API call. Smoke test live memerlukan `config.properties` yang valid; provider OpenAI-compatible gateway harus mendukung JSON/schema mode yang dikonfigurasi.
