# Panduan Developer: Menambah Field, Rules, dan Menangani Output yang Tidak Sesuai

Dokumen ini dibuat untuk memudahkan developer menambah field baru, menambah aturan bisnis, dan menangani kasus model menghasilkan output yang tidak sesuai kontrak.

## 1. Struktur aplikasi yang penting

Secara umum, pipeline ini dibagi menjadi dua bagian utama:

- Field fakta dokumen: semua hal yang diekstrak dari dokumen
- Rules bisnis: aturan keputusan seperti rawat inap vs rawat jalan, klaim baru vs kelengkapan, overseas, dll.

Lokasi utama:

- Field registry: [services/claim_fields.py](../services/claim_fields.py)
- Rulebook bisnis: [schemas/claim_rulebook.py](../schemas/claim_rulebook.py)
- Panduan detail field: [docs/langextract_claim_developer_guide.md](langextract_claim_developer_guide.md)
- Panduan detail rulebook: [docs/claim_rulebook.md](claim_rulebook.md)

---

## 2. Output final itu bukan raw extraction

Penting: di project ini ada 2 layer yang beda:

- raw contract / extracted facts = semua data yang benar-benar ditemukan di dokumen
- final output form = tampilan ringkas yang dibaca user / claim officer

Contoh sederhananya:

- raw extraction bisa punya banyak field seperti `admission_date`, `discharge_date`, `provider_name`, `diagnosis_text`
- final output bisa menampilkan hanya field yang relevan untuk form, misalnya `visitFrmDtm`, `visitToDtm`, `hphpName`, `incuredAmt`

Lokasi utama final output:

- [services/claim_form.py](../services/claim_form.py)
- [services/assessment_service.py](../services/assessment_service.py)

Prinsipnya:

1. raw facts tetap di `claim_data`
2. derived / computed values masuk ke `analyst_analysis`
3. final form hanya menampilkan subset yang dibutuhkan untuk UI / output akhir

Jadi kalau developer ingin output akhir berubah, biasanya yang diubah bukan raw extraction, melainkan mapping di final form layer.

### Rule of thumb

- kalau itu fakta dokumen: tambah ke field registry
- kalau itu keputusan bisnis: tambah ke rulebook
- kalau itu tampilan final: ubah mapping di `claim_form.py`

---

## 3. Cara menambah field

### Prinsip utamanya

Jangan menambah field secara sembarangan di prompt atau string LLM. Field harus ditambahkan di registry utama agar flow berikut otomatis ikut berubah:

- allowlist extraction
- normalizer
- grouping record
- validasi schema
- prompt generation

### Tempat yang benar

Edit file berikut:

- [services/claim_fields.py](../services/claim_fields.py)

Tambahkan `FieldSpec(...)` dengan format seperti berikut:

```python
FieldSpec(
    name="member_number",
    kind="scalar",
    field_type="identifier",
    family="identity",
    description="Nomor peserta polis; jangan tertukar dengan patient_id.",
)
```

### Aturan dasar

- `kind="scalar"` = satu nilai
- `kind="list"` = banyak nilai / occurrence
- `family` = kelompok field, misalnya `identity`, `claim`, `provider`
- `field_type` = tipe semantik: `text`, `identifier`, `date`, `amount`, `currency`, `label`
- `description` dipakai untuk mengurangi ambiguitas saat LLM mengekstrak

### Kapan field tidak boleh masuk allowlist

Jangan menambahkan field derived seperti:

- `risk_score`
- `is_fraud`
- `recommended_visit_type`
- `suggested_icd_code`

Field jenis itu seharusnya dihasilkan lewat `derive_claim_analysis()` dan masuk ke `analyst_analysis`, bukan ke `claim_data`.

### Quick checklist saat menambah field

- [ ] field sudah ditambahkan di `FIELD_SPECS`
- [ ] field masuk kategori `scalar` atau `list`
- [ ] kalau perlu, tambahkan `description`
- [ ] sesuaikan `family` bila relevan
- [ ] cek apakah field harus masuk `DATE_FIELDS` atau `NUMERIC_FIELDS`
- [ ] cek apakah field harus dimasukkan behavior normalizer
- [ ] tambahkan unit test

---

## 4. Cara menambah rules bisnis

### Prinsip utamanya

Rules bisnis tidak ditulis manual di beberapa tempat. Semua aturan dikelola di satu rulebook.

Lokasi:

- [schemas/claim_rulebook.py](../schemas/claim_rulebook.py)

### Apa peran rulebook?

Rulebook mendefinisikan bentuk dan nilai konfigurasi aturan. Ia dapat dipakai untuk:

- membuat deskripsi/schema yang dikirim ke LLM
- memvalidasi konfigurasi rule
- menyediakan nilai rule bagi fungsi business logic

Menambah field Pydantic saja belum membuat keputusan bisnis baru. Untuk rule yang benar-benar baru, implementasikan cara evaluasinya di service yang tepat dan tambahkan test yang membuktikan hasilnya. Jangan menganggap semua perubahan rulebook otomatis mengubah seluruh downstream.

### Contoh aturan yang ada

- `claim_types`
- `visit`
- `documents`
- `overseas`
- `period`
- `name_check`
- `confidence_tiers`

Contoh:

```python
class VisitRules(BaseModel):
    inpatient_labels: list[str] = Field(
        default_factory=lambda: ["rawat inap", "inpatient"],
        description="Label eksplisit rawat inap.",
    )
```

Untuk field yang memang dibaca oleh generator prompt/schema, deskripsinya ikut terbawa. Perubahan hasil keputusan tetap memerlukan logic yang membaca field rule tersebut.

### Langkah sederhana menambah rules

1. Buka [schemas/claim_rulebook.py](../schemas/claim_rulebook.py)
2. Tambah field baru di class yang sesuai
3. Beri `Field(description="...")`
4. Pastikan validasinya masuk akal (`ge=0`, `le=100`, dll.)
5. Jalankan test terkait

### Contoh kasus nyata: rawat inap beda tanggal => beda claim

Catatan: contoh ini menjelaskan kebutuhan bisnis, bukan perubahan yang sudah aktif hanya dengan menambah field. Field `separate_claim_when_inpatient_dates_differ` di bawah adalah ilustrasi; menambahkannya ke Pydantic saja tidak akan memisahkan claim. Implementasi perlu menentukan grouping/claim boundary di service yang membentuk claim, lalu diuji dengan dua episode berbeda.

Ini contoh paling gampang dipahami dalam project klaim: satu pasien punya 2 episode rawat inap di tanggal yang berbeda.

#### Skenario

- Episode 1: masuk `2026-01-05`, keluar `2026-01-10`
- Episode 2: masuk `2026-02-12`, keluar `2026-02-15`

Sistem harus memahami bahwa ini adalah 2 episode beda, bukan satu klaim yang digabung.

#### 1) Tambah field di registry

```python
FieldSpec(
    name="admission_date",
    kind="scalar",
    field_type="date",
    family="claim",
    description="Tanggal masuk rumah sakit untuk episode rawat inap.",
)

FieldSpec(
    name="discharge_date",
    kind="scalar",
    field_type="date",
    family="claim",
    description="Tanggal keluar rumah sakit untuk episode rawat inap.",
)
```

Tujuan: sistem punya data eksplisit untuk membedakan episode 1 dan episode 2.

#### 2) Tambah rule di rulebook

```python
class VisitRules(BaseModel):
    inpatient_labels: list[str] = Field(
        default_factory=lambda: ["rawat inap", "inpatient"],
        description="Label eksplisit rawat inap.",
    )
    inpatient_must_be_in_stay: bool = Field(
        default=True,
        description="Jika ada episode rawat inap, tanggal klaim harus berada di dalam rentang admission-discharge episode yang sama.",
    )
    separate_claim_when_inpatient_dates_differ: bool = Field(
        default=True,
        description="Jika tanggal masuk/keluar rawat inap berbeda antar episode, maka dianggap sebagai claim yang berbeda.",
    )
```

#### 3) Yang terjadi di logika

Sistem menerima data seperti ini:

```python
{
    "admission_date": "2026-01-05",
    "discharge_date": "2026-01-10",
    "visit_type": "inpatient"
}
```

lalu data lain:

```python
{
    "admission_date": "2026-02-12",
    "discharge_date": "2026-02-15",
    "visit_type": "inpatient"
}
```

Karena tanggalnya berbeda, aturan akan memutuskan:

- episode 1 = claim A
- episode 2 = claim B
- tidak boleh digabung jadi satu claim

#### 4) Dampak ke output final

Output form yang muncul bisa jadi seperti ini:

```python
{
    "visitFrmDtm": "2026-01-05",
    "visitToDtm": "2026-01-10",
    "typeOfVisit": "inpatient"
}
```

dan untuk episode kedua:

```python
{
    "visitFrmDtm": "2026-02-12",
    "visitToDtm": "2026-02-15",
    "typeOfVisit": "inpatient"
}
```

Jadi perubahan bukan cuma “menambah field di LLM”, tapi juga mengubah cara sistem memecah output akhir menjadi 2 claim yang benar.

---

### Contoh: menentukan claim type dari document_type halaman

Untuk alur inventory dokumen, penentuan `new` atau `completeness` memakai nilai `document_type` dari klasifikasi setiap halaman. Tidak perlu menambah field extraction atau rulebook baru untuk kasus ini.

#### Skenario A: ada formulir klaim

Salah satu halaman diklasifikasikan seperti ini:

```python
{
    "page": "1",
    "document_type": "Formulir Pengajuan Klaim",
    "category": None,
}
```

Karena `document_type` menyebut formulir klaim, hasilnya:

```json
{
  "claimType": "new",
  "isNewClaim": true,
  "isKelengkapan": false
}
```

Pencocokan menerima nama umum seperti `Formulir Klaim` dan `Formulir Pengajuan Klaim`. `category`, `label`, `title`, dan teks `SKD` saja bukan pemicu `new`.

#### Skenario B: tidak ada formulir klaim

Semua halaman berhasil diklasifikasikan, misalnya hanya ada `Invoice` dan `Resume Medis`:

```python
[
    {"page": "1", "document_type": "Invoice"},
    {"page": "2", "document_type": "Resume Medis"},
]
```

Karena tidak ada `document_type` yang menyebut formulir klaim, hasilnya `completeness`:

```json
{
  "claimType": "completeness",
  "isNewClaim": false,
  "isKelengkapan": true
}
```

#### Jika klasifikasi halaman belum lengkap

Kalau ada halaman yang belum berhasil diklasifikasikan, sistem tidak menyimpulkan `completeness`; hasil claim type tetap `unknown` agar tidak membuat keputusan dari inventory yang belum lengkap.

Implementasinya ada di `apply_document_claim_type()` pada [services/claim_form.py](../services/claim_form.py). Fungsi ini memperbarui `analyst_analysis.claim_case_type` dan decision flags yang dipakai output final. Ini aturan yang sudah ada; kalau kriteria bisnisnya berubah lagi, ubah logic tersebut dan tambahkan test untuk contoh dokumennya.

---

## 5. Cara memisah output ke blok seperti member_info, policy_info, atau header

Setelah fakta diekstrak dan keputusan bisnis dibuat, langkah berikutnya adalah memetakan data tersebut ke blok form yang lebih rapi.

Bukan semua field masuk ke satu payload besar. Di project ini output final dibagi menjadi blok seperti:

- `member_info`
- `policy_info`
- `claim`
- `visit`
- `provider`
- `medical`
- `financial`
- `service_lines`
- `review`

Lokasi utama:

- [services/claim_form.py](../services/claim_form.py)

### Contoh logikanya

Di [services/claim_form.py](../services/claim_form.py), ada fungsi seperti:

```python
def _member_block(claim_data, normalized):
    return {
        "insured_name": _first(
            _raw(claim_data, "patient_name"),
            _raw(claim_data, "policy_holder_name"),
        ),
        "dob": _value(claim_data, normalized, "date_of_birth"),
        "member_number": _raw(claim_data, "member_number"),
    }
```

Fungsi ini memisahkan field ke blok `member_info` supaya output form lebih rapi dan konsisten.

### Kenapa penting?

Karena kalau semua field ditaruh di satu level saja, output akan:

- sulit dibaca
- sulit dipetakan ke form UI
- susah di-maintain saat kontrak bertambah

### Kalau nanti ada blok baru

Misalnya atasan minta blok baru seperti:

- `beneficiary_info`
- `admission_summary`
- `claim_history`

Maka pola yang benar adalah:

1. cek field yang dibutuhkan
2. buat fungsi baru seperti `_beneficiary_block()`
3. masukkan ke return payload di `build_claim_form()`
4. pastikan field-nya diambil dari `claim_data` atau `analyst_analysis`, bukan dari raw string LLM yang acak

Contoh pola baru:

```python
def _beneficiary_block(claim_data, normalized):
    return {
        "beneficiary_name": _raw(claim_data, "beneficiary_name"),
        "relationship": _raw(claim_data, "relationship"),
    }
```

Lalu di `build_claim_form()`:

```python
return {
    "member_info": _member_block(claim_data, normalized),
    "policy_info": _policy_block(claim_data, normalized),
    "beneficiary_info": _beneficiary_block(claim_data, normalized),
}
```

Jadi developer tidak perlu membangun payload dari nol lagi. Cukup tambahkan blok baru dan mapping fieldnya.

---

## 6. Kalau output model tidak sesuai

Ini adalah bagian yang paling sering terjadi di project seperti ini. Cara pemecahan yang developer-friendly adalah:

### Kasus A: LLM ekstraksi field salah

Solusi:

- perjelas `description` field di [services/claim_fields.py](../services/claim_fields.py)
- pastikan field benar-benar ada di allowlist
- pastikan model tidak diarahkan untuk menebak value yang tidak eksplisit

### Kasus B: LLM memproduksi keputusan yang tidak masuk akal

Solusi:

- perbaiki rule di [schemas/claim_rulebook.py](../schemas/claim_rulebook.py)
- pastikan `reason` atau `evidence` jelas di output keputusan
- cek apakah aturan logika dibuat terlalu longgar atau terlalu ketat

### Kasus C: output ada field yang harus dihapus

Solusi:

- field derived jangan masuk ke `claim_data`
- field yang tidak eksplisit harus ditempatkan di `analyst_analysis` dengan status `derived`

### Kasus D: value terenkripsi atau salah format

Solusi:

- gunakan normalizer tanggal/angka sesuai field type
- jangan mengganti raw value asli di `claim_data`
- simpan bentuk normalisasi di field terpisah sesuai kontrak

---

## 7. Prinsip yang harus dipatuhi

### A. Fakta eksplisit vs inferred

- Fakta eksplisit: masuk ke `claim_data`
- Inferred value: masuk ke `analyst_analysis` dengan `status=derived`

Jangan mencampur keduanya di satu tempat.

### B. Raw value tidak diubah

Raw extraction harus tetap terjaga. Normalisasi dilakukan di layer terpisah.

### C. Kualitas output diuji, bukan hanya dilihat

Developer harus menambahkan test ketika menambah field atau rules.

---

## 8. Checklist developer sebelum merge

- [ ] field baru sudah ditambahkan di `FIELD_SPECS`
- [ ] deskripsi field jelas dan tidak ambigu
- [ ] field masuk `scalar` atau `list` dengan benar
- [ ] rules baru ada di rulebook, bukan di prompt manual
- [ ] output validation masih valid
- [ ] test terkait dijalankan
- [ ] tidak ada inferred value masuk ke `claim_data`
- [ ] dokumentasi diperbarui bila API/field berubah

---

## 9. Test yang relevan

Command yang biasanya dipakai:

```powershell
python -m unittest tests.test_langextract_service -v
```

Atau untuk suite yang lebih lengkap:

```powershell
.\.venv\Scripts\python -m unittest tests.test_claim_form tests.test_langextract_service tests.test_usage_tracker
```

---

## 10. Kesimpulan

Bila ingin menambah fitur baru di pipeline ini, pola yang benar adalah:

1. tambah field ke registry bila itu fakta dokumen
2. tambah aturan ke rulebook bila itu keputusan bisnis
3. pastikan deskripsi jelas agar LLM tidak bingung
4. validasi hasil dengan test
5. jangan mencampur inferred value dengan raw extraction

Dengan pola ini, project tetap maintainable dan developer-friendly.
