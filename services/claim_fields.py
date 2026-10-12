"""Central field/rule registry for the claim pipeline.

Developer entry point
---------------------
Add or change claim fields in ``FIELD_SPECS`` only. Everything downstream is
derived automatically:

- ``CLAIM_FIELDS``: LangExtract extraction allowlist;
- ``SCALAR_FIELDS`` / ``LIST_FIELDS``: contract shaping;
- ``DATE_FIELDS`` / ``NUMERIC_FIELDS``: normalization;
- ``FAMILY_BY_FIELD``: grouping family;
- ``GROUP_TYPES``: LangExtract group-type allowlist;
- ``list_destinations``: flat claim-data list targets;
- ``group_definitions``: relationship record definitions.

Field kinds
-----------
- ``scalar``: one value per contract, stored verbatim in ``claim_data``.
- ``list``: every grounded occurrence, stored verbatim in a ``*_list`` field.
- ``derived``: never emitted by LangExtract. These fields are computed in
  ``services.langextract_service.derive_claim_analysis`` and validated in the
  ``analyst_analysis`` section of the Pydantic contract.

The registry also enforces the explicit-versus-derived ownership rule: a field
may be scalar/list or derived, but never both.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal
FieldKind = Literal["scalar", "list", "group", "derived"]
FieldType = Literal["text", "identifier", "date", "amount", "currency", "label"]
@dataclass(frozen=True)
class FieldSpec:
    """Declarative specification for one claim-pipeline field."""

    name: str
    kind: FieldKind
    field_type: FieldType = "text"
    family: str = "none"
    derived_from: tuple[str, ...] = ()
    group_record_key: str | None = None
    list_destination: str | None = None
    description: str = ""
@dataclass(frozen=True)
class GroupDefinition:
    record_key: str
    family: str
    fields: tuple[str, ...]
    anchor_field: str
    extra_attributes: dict[str, Any] = field(default_factory=dict)
# The field registry is intentionally split into logical sections. This keeps the
# generated prompt, validation rules, and downstream field mapping easier to scan
# and maintain without changing the contract semantics.

_IDENTITY_FIELDS: tuple[FieldSpec, ...] = (
    # A few names are self-explanatory and deliberately left without a
    # description to keep the prompt budget down. Ambiguous or easily confused
    # fields always carry one.
    FieldSpec(name="policy_holder_name", kind="scalar", family="identity"),
    FieldSpec(name="policy_number", kind="scalar", field_type="identifier", family="identity"),
    FieldSpec(
        name="member_number",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor peserta polis; jangan tertukar dengan patient_id.",
    ),
    FieldSpec(name="patient_name", kind="scalar", family="identity"),
    FieldSpec(
        name="patient_id",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor rekam medis pasien di rumah sakit.",
    ),
    FieldSpec(name="date_of_birth", kind="scalar", field_type="date", family="identity"),
    FieldSpec(
        name="client_no",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor client perusahaan; berbeda dari patient_id.",
    ),
    FieldSpec(
        name="medical_record_number",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor rekam medis/RS; berbeda dari patient_id.",
    ),
    FieldSpec(name="address", kind="scalar", family="identity"),
    FieldSpec(
        name="email",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description=(
            "Alamat email yang secara eksplisit milik tertanggung/pasien. "
            "Jangan ekstrak email rumah sakit, dokter, atau agen sebagai email pasien. "
            "Jika pemilik email tidak jelas, jangan isi field ini."
        ),
    ),
    FieldSpec(
        name="telephone",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor telepon landline Tertanggung/pasien.",
    ),
    FieldSpec(
        name="mobile_phone",
        kind="scalar",
        field_type="identifier",
        family="identity",
        description="Nomor telepon seluler Tertanggung/pasien.",
    ),
    FieldSpec(
        name="payee_type",
        kind="scalar",
        field_type="label",
        family="identity",
        description="Pihak penerima dana, misalnya subscriber atau provider.",
    ),
)

_POLICY_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="policy_effective_date",
        kind="scalar",
        field_type="date",
        family="policy",
        description="Tanggal mulai berlaku polis.",
    ),
    FieldSpec(
        name="policy_age",
        kind="scalar",
        family="policy",
        description="Umur polis dalam tahun saat polis berlaku.",
    ),
    FieldSpec(
        name="agent_no",
        kind="scalar",
        field_type="identifier",
        family="policy",
        description="Nomor agen penjual polis.",
    ),
)

_PROVIDER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="provider_name", kind="scalar", family="provider"),
    FieldSpec(name="provider_code", kind="scalar", field_type="identifier", family="provider"),
    FieldSpec(
        name="doctor_name",
        kind="list",
        family="provider",
        list_destination="doctor_names",
        description="Nama dokter yang tertulis di dokumen.",
    ),
    FieldSpec(
        name="service_procedure_code",
        kind="scalar",
        field_type="identifier",
        family="provider",
        description="Kode layanan/prosedur dari tabel provider.",
    ),
    FieldSpec(
        name="service_procedure_desc",
        kind="scalar",
        family="provider",
        description="Nama layanan/prosedur dalam kalimat.",
    ),
)

_CLAIM_HEADER_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="claim_number", kind="scalar", field_type="identifier", family="claim"),
    FieldSpec(
        name="sub_claim_no",
        kind="scalar",
        field_type="identifier",
        family="claim",
        description="Nomor klaim turunan dari dokumen sebelumnya.",
    ),
    FieldSpec(
        name="case_no",
        kind="scalar",
        field_type="identifier",
        family="claim",
        description="Nomor kasus/docket internal kantor.",
    ),
    FieldSpec(
        name="precert_no",
        kind="scalar",
        field_type="identifier",
        family="claim",
        description="Nomor prapengesahan sebelum perawatan.",
    ),
    FieldSpec(
        name="claim_type",
        kind="scalar",
        field_type="label",
        family="claim",
        description=(
            "Jenis klaim yang tertulis, misalnya SKD, formulir, reimbursement, "
            "atau kelengkapan."
        ),
    ),
    FieldSpec(
        name="visit_type",
        kind="scalar",
        field_type="label",
        family="claim",
        description=(
            "Hanya bila dokumen menulis rawat inap/jalan atau inpatient/outpatient."
        ),
    ),
    FieldSpec(
        name="admission_date",
        kind="scalar",
        field_type="date",
        family="claim",
        description="Tanggal masuk dirawat untuk rawat inap.",
    ),
    FieldSpec(
        name="discharge_date",
        kind="scalar",
        field_type="date",
        family="claim",
        description="Tanggal keluar dirawat untuk rawat inap.",
    ),
    FieldSpec(
        name="hospitalization_date",
        kind="scalar",
        field_type="date",
        family="claim",
        description="Tanggal layanan; untuk rawat jalan isi tanggal kwitansi.",
    ),
    FieldSpec(
        name="received_date",
        kind="scalar",
        field_type="date",
        family="claim",
        description="Tanggal dokumen diterima kantor.",
    ),
    FieldSpec(
        name="accident_date",
        kind="scalar",
        field_type="date",
        family="claim",
        description="Tanggal kecelakaan bila klaim berkaitan kecelakaan.",
    ),
    FieldSpec(
        name="account_no",
        kind="scalar",
        field_type="identifier",
        family="claim",
        description="Nomor rekening tujuan pembayaran.",
    ),
    FieldSpec(
        name="account_name",
        kind="scalar",
        family="claim",
        description="Nama pemilik rekening.",
    ),
    FieldSpec(
        name="bank_name",
        kind="scalar",
        family="claim",
        description="Nama bank pemilik rekening.",
    ),
    FieldSpec(
        name="branch_name",
        kind="scalar",
        family="claim",
        description="Cabang bank dari rekening tersebut.",
    ),
    FieldSpec(
        name="reason_of_visit",
        kind="scalar",
        family="claim",
        description="Alasan kunjungan yang tertulis di dokumen.",
    ),
)

_DIAGNOSIS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="diagnosis", kind="list", family="diagnosis", list_destination="diagnosis"),
    FieldSpec(
        name="icd_code",
        kind="list",
        field_type="identifier",
        family="diagnosis",
        list_destination="icd_codes",
        description="Kode ICD yang tertulis; jangan menyimpulkan dari diagnosis.",
    ),
    FieldSpec(
        name="medical_history",
        kind="list",
        family="diagnosis",
        list_destination="medical_history",
        description="Riwayat penyakit yang tertulis di dokumen.",
    ),
    FieldSpec(
        name="pre_existing_condition",
        kind="list",
        family="diagnosis",
        list_destination="pre_existing_conditions",
        description="Penyakit yang sudah ada sebelum klaim.",
    ),
)

_MEDICATION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="medication",
        kind="list",
        family="medication",
        list_destination="medications",
        description="Nama obat yang diberikan.",
    ),
    FieldSpec(
        name="medication_record",
        kind="group",
        family="medication",
        group_record_key="medication_records",
        derived_from=("medication", "dosage", "frequency", "duration"),
    ),
    FieldSpec(name="dosage", kind="scalar", family="medication"),
    FieldSpec(name="frequency", kind="scalar", family="medication"),
    FieldSpec(name="duration", kind="scalar", family="medication"),
)

_PROCEDURE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="procedure",
        kind="list",
        family="procedure",
        list_destination="procedures",
        description="Nama tindakan/prosedur yang dilakukan.",
    ),
    FieldSpec(
        name="treatment",
        kind="list",
        family="procedure",
        list_destination="treatments",
        description="Nama perawatan yang diberikan.",
    ),
)

_LAB_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="laboratory_result", kind="scalar", family="lab_result"),
    FieldSpec(
        name="lab_result",
        kind="group",
        family="lab_result",
        group_record_key="lab_results",
        derived_from=("laboratory_result",),
    ),
)

_INVOICE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="invoice_number", kind="scalar", field_type="identifier", family="invoice"),
    FieldSpec(name="invoice_date", kind="scalar", field_type="date", family="invoice"),
    FieldSpec(name="subtotal", kind="scalar", field_type="amount", family="invoice"),
    FieldSpec(name="tax", kind="scalar", field_type="amount", family="invoice"),
    FieldSpec(
        name="total_amount",
        kind="scalar",
        field_type="amount",
        family="invoice",
        description="Total tagihan akhir pada invoice.",
    ),
    FieldSpec(
        name="currency",
        kind="scalar",
        field_type="currency",
        family="invoice",
        description="Mata uang tagihan; default IDR bila tidak tertulis.",
    ),
    FieldSpec(
        name="amount_incurred",
        kind="scalar",
        field_type="amount",
        family="invoice",
        description="Nominal klaim yang diajukan; default IDR.",
    ),
    FieldSpec(
        name="exchange_rate",
        kind="scalar",
        field_type="amount",
        family="invoice",
        description="Kurs ke IDR; default 1 bila IDR.",
    ),
    FieldSpec(
        name="exchange_rate_date",
        kind="scalar",
        field_type="date",
        family="invoice",
        description="Tanggal kurs yang berlaku.",
    ),
    FieldSpec(
        name="service_date_from",
        kind="scalar",
        field_type="date",
        family="invoice",
        description="Tanggal awal baris layanan.",
    ),
    FieldSpec(
        name="service_date_to",
        kind="scalar",
        field_type="date",
        family="invoice",
        description="Tanggal akhir baris layanan.",
    ),
    FieldSpec(
        name="other_insurance_amount",
        kind="scalar",
        field_type="amount",
        family="invoice",
        description="Nominal yang dibayar asuransi lain.",
    ),
    FieldSpec(
        name="discount_amount",
        kind="scalar",
        field_type="amount",
        family="invoice",
        description="Potongan/diskon yang berlaku.",
    ),
)

_DOCUMENT_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="supporting_document",
        kind="list",
        family="document",
        list_destination="supporting_documents",
        description="Nama dokumen pendukung yang disebut di teks.",
    ),
)

_DERIVED_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="visit_type_analysis",
        kind="derived",
        field_type="label",
        family="claim",
        derived_from=("visit_type", "admission_date", "discharge_date"),
    ),
    FieldSpec(
        name="claim_case_type",
        kind="derived",
        field_type="label",
        family="claim",
        derived_from=("claim_type",),
    ),
    FieldSpec(
        name="document_icd_code",
        kind="derived",
        field_type="identifier",
        family="diagnosis",
        derived_from=("diagnosis", "icd_code"),
    ),
    FieldSpec(
        name="suggested_icd_code",
        kind="derived",
        field_type="identifier",
        family="diagnosis",
        derived_from=("diagnosis",),
    ),
)

FIELD_SPECS: tuple[FieldSpec, ...] = (
    *_IDENTITY_FIELDS,
    *_POLICY_FIELDS,
    *_PROVIDER_FIELDS,
    *_CLAIM_HEADER_FIELDS,
    *_DIAGNOSIS_FIELDS,
    *_MEDICATION_FIELDS,
    *_PROCEDURE_FIELDS,
    *_LAB_FIELDS,
    *_INVOICE_FIELDS,
    *_DOCUMENT_FIELDS,
    *_DERIVED_FIELDS,
)
GROUP_DEFINITIONS: tuple[GroupDefinition, ...] = (
    GroupDefinition(record_key="diagnosis_records", family="diagnosis", fields=("diagnosis", "icd_code"), anchor_field="diagnosis", extra_attributes={"document_icd_code_from": "icd_code", "icd_code_records_from": "icd_code"}),
    GroupDefinition(record_key="medication_records", family="medication", fields=("medication", "dosage", "frequency", "duration"), anchor_field="medication"),
    GroupDefinition(record_key="procedure_records", family="procedure", fields=("procedure", "treatment"), anchor_field="procedure"),
    GroupDefinition(record_key="invoice_records", family="invoice", fields=("invoice_number", "invoice_date", "subtotal", "tax", "total_amount", "currency"), anchor_field="invoice_number"),
    GroupDefinition(record_key="lab_results", family="lab_result", fields=("laboratory_result",), anchor_field="laboratory_result"),
)
def _unique(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)
def field_specs() -> tuple[FieldSpec, ...]:
    return FIELD_SPECS
def extraction_specs() -> tuple[FieldSpec, ...]:
    return tuple(spec for spec in FIELD_SPECS if spec.kind in {"scalar", "list"})
def derived_specs() -> tuple[FieldSpec, ...]:
    return tuple(spec for spec in FIELD_SPECS if spec.kind in {"group", "derived"})
def spec_by_name(name: str) -> FieldSpec:
    for spec in FIELD_SPECS:
        if spec.name == name:
            return spec
    raise KeyError(f"Unknown claim field: {name!r}")
def claim_fields() -> tuple[str, ...]:
    return tuple(spec.name for spec in extraction_specs())
def field_descriptions() -> dict[str, str]:
    """Map extraction field name to its prompt/schema description.

    Only fields that carry a description appear here.  Self-explanatory names
    are intentionally omitted so the prompt and output schema stay small; the
    savings are re-spent on the fields that are actually ambiguous.
    """
    return {spec.name: spec.description for spec in extraction_specs() if spec.description}
def described_fields() -> tuple[str, ...]:
    """Extraction field names that have a description, in registry order."""
    return tuple(spec.name for spec in extraction_specs() if spec.description)
def undescribed_fields() -> tuple[str, ...]:
    """Extraction field names deliberately left without a description."""
    return tuple(spec.name for spec in extraction_specs() if not spec.description)
def scalar_fields() -> tuple[str, ...]:
    return tuple(spec.name for spec in FIELD_SPECS if spec.kind == "scalar")
def list_fields() -> tuple[str, ...]:
    return tuple(spec.name for spec in FIELD_SPECS if spec.kind == "list")
def derived_fields() -> tuple[str, ...]:
    return tuple(spec.name for spec in FIELD_SPECS if spec.kind == "derived")
def group_record_fields() -> tuple[str, ...]:
    return tuple(spec.name for spec in FIELD_SPECS if spec.kind == "group")
def group_record_keys() -> tuple[str, ...]:
    return tuple(spec.group_record_key for spec in FIELD_SPECS if spec.kind == "group" and spec.group_record_key)
def date_fields() -> frozenset[str]:
    return frozenset(spec.name for spec in extraction_specs() if spec.field_type == "date")
def numeric_fields() -> frozenset[str]:
    return frozenset(spec.name for spec in extraction_specs() if spec.field_type == "amount")
def family_by_field() -> dict[str, str]:
    return {spec.name: spec.family for spec in extraction_specs()}
def group_types() -> tuple[str, ...]:
    families = _unique(tuple(spec.family for spec in extraction_specs() if spec.family != "none"))
    return families + ("none",)
def list_destinations() -> dict[str, str]:
    destinations: dict[str, str] = {}
    for spec in extraction_specs():
        if spec.kind == "list":
            destinations[spec.name] = spec.list_destination or spec.name
    return destinations
def group_definitions() -> tuple[GroupDefinition, ...]:
    return GROUP_DEFINITIONS
def validate_registry() -> list[str]:
    errors: list[str] = []
    names = [spec.name for spec in FIELD_SPECS]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate field names: {', '.join(duplicates)}")
    for spec in FIELD_SPECS:
        if spec.kind == "list" and not spec.list_destination:
            errors.append(f"list field {spec.name!r} needs list_destination")
        if spec.kind in {"scalar", "group", "derived"} and spec.list_destination:
            errors.append(f"{spec.kind} field {spec.name!r} must not set list_destination")
        if spec.kind in {"group", "derived"} and not spec.derived_from:
            errors.append(f"{spec.kind} field {spec.name!r} needs derived_from source fields")
        if spec.kind in {"scalar", "list"} and spec.derived_from:
            errors.append(f"explicit field {spec.name!r} must not set derived_from")
    for spec in FIELD_SPECS:
        # Descriptions reach the model through the prompt and output schema, so
        # only extraction fields may carry one. A description on a derived field
        # would wrongly invite the model to emit it.
        if spec.description and spec.kind not in {"scalar", "list"}:
            errors.append(
                f"{spec.kind} field {spec.name!r} must not set description; "
                "only extracted fields are described to the model"
            )
        if spec.kind == "group" and not spec.group_record_key:
            errors.append(f"group field {spec.name!r} needs group_record_key")
        if spec.kind == "group" and spec.group_record_key:
            matches = [d.record_key for d in GROUP_DEFINITIONS if d.record_key == spec.group_record_key]
            if not matches:
                errors.append(f"group field {spec.name!r} record {spec.group_record_key!r} missing from GROUP_DEFINITIONS")
    known_fields = set(names)
    for definition in GROUP_DEFINITIONS:
        unknown = [name for name in definition.fields if name not in known_fields]
        if unknown:
            errors.append(f"group {definition.record_key!r} uses unknown fields: {', '.join(unknown)}")
        if definition.anchor_field not in definition.fields:
            errors.append(f"group {definition.record_key!r} anchor {definition.anchor_field!r} is not in its fields")
    return errors
__all__ = ["FIELD_SPECS", "GROUP_DEFINITIONS", "FieldSpec", "GroupDefinition", "claim_fields", "date_fields", "derived_fields", "derived_specs", "extraction_specs", "family_by_field", "field_specs", "group_definitions", "group_record_fields", "group_record_keys", "group_types", "list_destinations", "list_fields", "numeric_fields", "scalar_fields", "spec_by_name", "validate_registry"]



