from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Medication(BaseModel):
    service_date: Optional[str] = Field(default=None, examples=["11.03.2025"])
    medicine_name: Optional[str] = Field(default=None, examples=["Paracetamol"])
    quantity: Optional[float] = Field(default=None, examples=[15])
    unit: Optional[str] = Field(default=None, examples=["TAB"])
    doctor_name: Optional[str] = Field(default=None, examples=["dr. Jane Doe"])
    amount: Optional[float] = Field(default=None, examples=[37500])


class Medicine(BaseModel):
    name: Optional[str] = Field(default=None, examples=["Paracetamol"])
    dosage: Optional[str] = Field(default=None, examples=["500 mg"])
    date: Optional[str] = Field(default=None, examples=["11.03.2025"])
    doctor: Optional[str] = Field(default=None, examples=["dr. Jane Doe"])
    unit: Optional[str] = Field(default=None, examples=["TAB"])
    frequency: Optional[str] = Field(default=None, examples=["3 kali sehari"])
    duration: Optional[str] = Field(default=None, examples=["5 hari"])
    quantity: Optional[float] = Field(default=None, examples=[15])
    unit_price: Optional[float] = Field(default=None, examples=[2500])
    total_price: Optional[float] = Field(default=None, examples=[37500])
    source_page: Optional[int] = Field(default=None, examples=[4])
    source_text: Optional[str] = Field(default=None, examples=["OTSU SODIUM CHLORIDA"])


class LineItem(BaseModel):
    description: Optional[str] = Field(default=None, examples=["Consultation"])
    quantity: Optional[float] = Field(default=None, examples=[1])
    unit_price: Optional[float] = Field(default=None, examples=[100000])
    amount: Optional[float] = Field(default=None, examples=[100000])


class LabResult(BaseModel):
    test_name: Optional[str] = Field(default=None, examples=["Hemoglobin"])
    result: Optional[str] = Field(default=None, examples=["12.7"])
    unit: Optional[str] = Field(default=None, examples=["g/dL"])
    reference_range: Optional[str] = Field(default=None, examples=["11.6 - 15.0"])
    abnormal: Optional[bool] = Field(default=None, examples=[False])


class EvidenceItem(BaseModel):
    statement: str = Field(examples=["Patient name appears on the laboratory report"])
    source_pages: list[int] = Field(default_factory=list, examples=[[2, 3]])


class PageExtraction(BaseModel):
    page_number: int = Field(examples=[1])
    document_type: str = Field(examples=["Hospital receipt"])
    extracted: dict[str, Any] = Field(default_factory=dict)


class DocumentExtraction(BaseModel):
    document_type: str = Field(examples=["Insurance claim document"])
    summary: str = Field(examples=["Three-page claim package containing a receipt and laboratory report."])
    pages: list[PageExtraction] = Field(default_factory=list)
    combined_extraction: dict[str, Any] = Field(default_factory=dict)
    analyst_notes: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    inconsistencies: list[str] = Field(default_factory=list)
    analyst_questions: list[str] = Field(default_factory=list)


class ClaimAnalysis(BaseModel):
    claim_case_type: Optional[str] = Field(default=None, examples=["NEW"])
    visit_type: Optional[str] = Field(default=None, examples=["INPATIENT"])
    documents_found: list[str] = Field(default_factory=list, examples=[["Claim Form", "Invoice"]])
    documents_missing: list[str] = Field(default_factory=list, examples=[["SEP BPJS"]])

    insured_name: Optional[str] = Field(default=None, examples=["John Doe"])
    policy_holder_name: Optional[str] = Field(default=None, examples=["John Doe"])
    policy_number: Optional[str] = Field(default=None, examples=["POL-12345"])
    member_number: Optional[str] = Field(default=None, examples=["0102593392"])
    date_of_birth: Optional[str] = Field(default=None, examples=["1990-01-15"])
    medical_record_number: Optional[str] = Field(default=None, examples=["MR-001"])
    address: Optional[str] = Field(default=None, examples=["Jakarta"])

    provider_name: Optional[str] = Field(default=None, examples=["ABC Hospital"])
    provider_code: Optional[str] = Field(default=None, examples=["HSP-001"])
    provider_country: Optional[str] = Field(default=None, examples=["Indonesia"])
    doctor_name: Optional[str] = Field(default=None, examples=["Dr. Jane Doe"])
    doctor_names: list[str] = Field(default_factory=list, examples=[["Dr. Jane Doe", "Dr. John Smith"]])

    hospitalization_date: Optional[str] = Field(default=None, examples=["2026-01-15"])
    admission_date: Optional[str] = Field(default=None, examples=["2026-01-15"], description="Admission Date ")
    discharge_date: Optional[str] = Field(default=None, examples=["2026-01-18"])
    length_of_stay_days: Optional[int] = Field(default=None, examples=[3])
    accident_date: Optional[str] = Field(default=None, examples=["2026-01-10"])

    reason_of_visit: Optional[str] = Field(default=None, examples=["Follow-up treatment"])
    chief_complaint: Optional[str] = Field(default=None, examples=["Chest pain"])
    accident_related: Optional[bool] = Field(default=None, examples=[False])
    accident_description: Optional[str] = Field(default=None, examples=["No accident related"])
    diagnosis_primary: Optional[str] = Field(default=None, examples=["Dengue fever"])
    diagnosis_secondary: list[str] = Field(default_factory=list, examples=[["Hypertension"]])
    icd_codes: list[str] = Field(default_factory=list, examples=[["I10", "E14.9"]])
    medical_history: list[str] = Field(default_factory=list, examples=[["History of hypertension"]])
    pre_existing_indications: list[str] = Field(default_factory=list, examples=[["Diabetes history"]])

    amount_incurred: Optional[float] = Field(default=None, examples=[1110000])
    currency: Optional[str] = Field(default=None, examples=["IDR"])
    exchange_rate: Optional[float] = Field(default=None, examples=[1.0])
    room_charge: Optional[float] = Field(default=None, examples=[500000])
    medicine_charge: Optional[float] = Field(default=None, examples=[200000])
    consultation_charge: Optional[float] = Field(default=None, examples=[150000])
    procedure_charge: Optional[float] = Field(default=None, examples=[250000])
    lab_charge: Optional[float] = Field(default=None, examples=[75000])
    radiology_charge: Optional[float] = Field(default=None, examples=[125000])

    procedures: list[str] = Field(default_factory=list, examples=[["CT Scan"]])
    surgery_performed: Optional[bool] = Field(default=None, examples=[False])
    surgery_name: Optional[str] = Field(default=None, examples=["Appendectomy"])

    medications: list[Medication] = Field(default_factory=list)
    diagnosis: list[str] = Field(default_factory=list, examples=[["Dengue fever"]])
    treatments: list[str] = Field(default_factory=list, examples=[["Inpatient care"]])
    medicines: list[Medicine] = Field(default_factory=list)
    lab_results: list[LabResult] = Field(default_factory=list)
    supporting_documents: list[str] = Field(default_factory=list)

    document_type: Optional[str] = Field(default=None, examples=["Health insurance claim"])
    claimant: Optional[str] = Field(default=None, examples=["John Doe"])
    patient_name: Optional[str] = Field(default=None, examples=["John Doe"])
    patient_id: Optional[str] = Field(default=None, examples=["0102593392"])
    insurer: Optional[str] = Field(default=None, examples=["Example Insurance"])
    policy_number: Optional[str] = Field(default=None, examples=["POL-12345"])
    claim_number: Optional[str] = Field(default=None, examples=["CLM-12345"])
    provider: Optional[str] = Field(default=None, examples=["ABC Hospital"])
    admission_date: Optional[str] = Field(default=None, examples=["2026-01-15"])
    discharge_date: Optional[str] = Field(default=None, examples=["2026-01-18"])
    invoice_number: Optional[str] = Field(default=None, examples=["INV-1001"])
    invoice_date: Optional[str] = Field(default=None, examples=["2026-01-15"])
    subtotal: Optional[float] = Field(default=None, examples=[1000000])
    tax: Optional[float] = Field(default=None, examples=[110000])
    total_amount: Optional[float] = Field(default=None, examples=[1110000])
    document_summary: Optional[str] = Field(default=None, examples=["Receipt and laboratory report for one outpatient claim."])
    cross_page_findings: list[EvidenceItem] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    inconsistencies: list[str] = Field(default_factory=list)
    analyst_questions: list[str] = Field(default_factory=list)

    risk_category: Optional[str] = Field(default=None, examples=["LOW"])
    risk_percentage: Optional[float] = Field(default=None, examples=[12.5])
    potential_risks: list[str] = Field(default_factory=list, examples=[["Pre-existing condition review"]])
    fraud_indicators: list[str] = Field(default_factory=list)
    pre_existing_indicators: list[str] = Field(default_factory=list)
    investigation_recommended: Optional[bool] = Field(default=None, examples=[False])

    case_snapshot: Optional[str] = Field(default=None, examples=["OUTPATIENT\nProvider: MITRA KELUARGA PONDOK TJANDRA"])
    medical_summary: Optional[str] = Field(default=None, examples=["Pasien melakukan control rawat jalan."])
    financial_summary: Optional[str] = Field(default=None, examples=["Total biaya Rp1.215.882."])
    claim_story: Optional[str] = Field(default=None, examples=["Pasien menjalani rawat jalan pada 31 Agustus 2026."])


InsuranceClaim = ClaimAnalysis


class Invoice(BaseModel):
    document_type: Optional[str] = Field(default=None, examples=["Commercial invoice"])
    invoice_number: Optional[str] = Field(default=None, examples=["INV-1001"])
    invoice_date: Optional[str] = Field(default=None, examples=["2026-01-15"])
    due_date: Optional[str] = Field(default=None, examples=["2026-02-15"])
    seller: Optional[str] = Field(default=None, examples=["Example Supplier"])
    buyer: Optional[str] = Field(default=None, examples=["Example Buyer"])
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = Field(default=None, examples=[1000000])
    tax: Optional[float] = Field(default=None, examples=[110000])
    total_amount: Optional[float] = Field(default=None, examples=[1110000])
    currency: Optional[str] = Field(default=None, examples=["IDR"])


class MedicalDocument(BaseModel):
    document_type: Optional[str] = Field(default=None, examples=["Medical prescription"])
    patient: Optional[str] = Field(default=None, examples=["John Doe"])
    doctor: Optional[str] = Field(default=None, examples=["Dr. Jane Doe"])
    diagnosis: list[str] = Field(default_factory=list, examples=[["Flu"]])
    medicines: list[Medicine] = Field(default_factory=list)
    treatments: list[str] = Field(default_factory=list, examples=[["Rest"]])
    document_date: Optional[str] = Field(default=None, examples=["2026-01-15"])


class GeneralDocument(BaseModel):
    document_type: Optional[str] = Field(default=None, examples=["Report"])
    title: Optional[str] = Field(default=None, examples=["Annual Report"])
    organization: Optional[str] = Field(default=None, examples=["Example Company"])
    people: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)


BpjsDocument = InsuranceClaim

NATIVE_SCHEMA_REGISTRY = {
    "Insurance claim": InsuranceClaim,
    "Claim analysis": InsuranceClaim,
    "InsuranceClaim": InsuranceClaim,
    "ClaimAnalysis": InsuranceClaim,
    "Invoice": Invoice,
    "Medical document": MedicalDocument,
    "General document": GeneralDocument,
}
