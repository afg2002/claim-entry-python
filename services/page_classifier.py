"""Model-driven page naming for the claim document inventory.

The claim form needs to know what every page in the file actually is, so the
model is asked to name each page from its own content. An earlier attempt used
a closed keyword/enum vocabulary and was rejected: it could only ever answer
"other" for a document outside the list, and a catch-all label is not an answer
a claim officer can act on.

Two properties matter here:

* **The model names the document, nothing is invented here.** ``document_type``
  is a free string taken verbatim from the model. If the model does not answer
  for a page, that page is reported as unclassified instead of being given a
  fabricated name.
* **Business logic uses a separate, narrow channel.** ``category`` is a small
  controlled vocabulary used only to infer inpatient versus outpatient. It may
  be ``null``; the document name never depends on it.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, Field

from config import AppConfig

# Narrow, business-meaningful categories. Deliberately has no catch-all: a page
# that matches none of these simply has category=null, which keeps the visit
# type honest instead of forcing it into a bucket it does not belong to.
DocumentCategory = Literal[
    "formulir_klaim",
    "invoice",
    "discharge_letter",
    "resume",
    "prescription",
    "lab_result",
    "referral",
]

DOCUMENT_CATEGORIES: tuple[str, ...] = (
    "formulir_klaim",
    "invoice",
    "discharge_letter",
    "resume",
    "prescription",
    "lab_result",
    "referral",
)

# Words that mean "the model gave no useful name". They are treated as an
# absent answer rather than stored as a document type.
_NON_ANSWERS: frozenset[str] = frozenset(
    {
        "",
        "other",
        "unknown",
        "unclassified",
        "document",
        "dokumen",
        "lainnya",
        "tidak diketahui",
        "n/a",
        "na",
        "null",
        "none",
    }
)


class PageClassification(BaseModel):
    """One page as the model names it.

    All guidance lives in the field descriptions rather than in a long prompt:
    the SDK turns this model into a strict JSON schema, and the description of
    each field is carried into that schema, so the model is guided by the
    contract it has to satisfy.
    """

    page: str | None = Field(
        default=None,
        description="Nomor halaman, persis seperti yang ditulis pada penanda halaman.",
    )
    document_type: str = Field(
        min_length=1,
        description=(
            "Nama dokumen sesuai judulnya sendiri, ditulis dalam bahasa "
            "dokumen. Contoh: 'Surat Tugas Dokter', 'Formulir Pengajuan Klaim', "
            "'Rincian Tagihan', 'Hasil Laboratorium Darah', 'Kwitansi Rawat Jalan'. "
            "Jangan pernah menjawab 'other', 'unknown', atau 'dokumen'; "
            "sebutkan nama sesungguhnya."
        ),
    )
    category: DocumentCategory | None = Field(
        default=None,
        description=(
            "Hanya untuk logika alur klaim (rawat inap atau rawat jalan). "
            "Isi hanya bila dokumen benar-benar cocok, selain itu null. "
            "Jangan memaksa kecocokan yang tidak tepat."
        ),
    )
    title: str | None = Field(
        default=None,
        description="Judul halaman apa adanya bila ada, jika tidak null.",
    )


class PageInventory(BaseModel):
    """The document inventory, one entry per page."""

    pages: list[PageClassification] = Field(
        description="Satu entri untuk setiap halaman yang diberikan, tidak ada halaman yang dilewati."
    )


_PAGE_MARKER = "## Page {number}"


def build_page_classification_prompt() -> str:
    """One sentence: every instruction lives in the response model instead.

    The field descriptions on ``PageClassification`` are carried into the
    generated JSON schema, so repeating them here would only spend tokens on
    guidance the model already receives in the contract.
    """
    return (
        "Name what each page of this insurance claim file actually is. "
        "Follow the field descriptions exactly."
    )


def _build_page_digest(pages: Sequence[Mapping[str, Any]]) -> tuple[str, list[int]]:
    """Join a bounded head of every page into one prompt input.

    Only the first part of each page is sent: a page title and its table
    headers are enough to identify the document, and sending whole clinical
    notes would cost far more than it adds.
    """
    sections: list[str] = []
    offsets: list[int] = []
    for page in pages:
        page_number = page.get("page_number")
        text = str(page.get("raw_text") or "")
        head = "\n".join(text.splitlines()[:40])[:2000]
        block = f"{_PAGE_MARKER.format(number=page_number)}\n{head}"
        offsets.append(len(block))
        sections.append(block)
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Model call
# ---------------------------------------------------------------------------

def _response_schema() -> dict[str, Any]:
    """The strict JSON schema the SDK derives from ``PageInventory``.

    ``document_type`` stays a free string on purpose: constraining it to an enum
    would force documents outside the list into a catch-all label, which tells
    a reviewer nothing. ``category`` stays constrained because it only feeds
    workflow logic and is allowed to be null.
    """
    from openai.lib._parsing._completions import type_to_response_format_param

    return dict(type_to_response_format_param(PageInventory))


def _normalize_page_key(value: Any, pages: Sequence[Mapping[str, Any]]) -> str | None:
    """Match a model-returned page reference back to a real page number."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    known = {str(page.get("page_number")) for page in pages}
    if text in known:
        return text
    digits = "".join(character for character in text if character.isdigit())
    if digits and digits in known:
        return digits
    return None


def _clean_document_type(value: Any) -> str | None:
    """Return the model's document name, or ``None`` when it named nothing.

    The name is never rewritten: whatever the model wrote is what the reviewer
    sees. Only non-answers ("other", "unknown", an empty string) are treated as
    an absent answer, because storing those would present a non-answer as if it
    were a finding.
    """
    text = str(value or "").strip()
    if text.lower() in _NON_ANSWERS:
        return None
    return text


def _clean_category(value: Any) -> str | None:
    """Return the business category, or ``None`` when the model left it open."""
    text = str(value or "").strip().lower()
    return text if text in DOCUMENT_CATEGORIES else None


def _call_model(
    config: AppConfig,
    digest: str,
    *,
    client: Any,
) -> list[dict[str, Any]]:
    from openai import OpenAI

    active_client = client or OpenAI(
        api_key=config.api_key or config.langextract_api_key,
        base_url=config.base_url or config.langextract_base_url,
    )
    response = active_client.chat.completions.create(
        model=config.extraction_model or config.langextract_model,
        messages=[
            {"role": "system", "content": build_page_classification_prompt()},
            {"role": "user", "content": digest},
        ],
        response_format=_response_schema(),
    )
    content = response.choices[0].message.content or ""
    # Validate through the same model that produced the schema, so a malformed
    # answer fails here instead of leaking half-parsed rows downstream.
    parsed = PageInventory.model_validate_json(content)
    return [item.model_dump() for item in parsed.pages]


def classify_pages(
    pages: Sequence[Mapping[str, Any]],
    config: AppConfig,
    *,
    client: Any = None,
) -> list[dict[str, Any]]:
    """Return one entry per page, carrying the model's own document name.

    Every page is always represented. A page the model failed to name keeps
    ``document_type=None`` with ``source="not_named"`` so the gap is visible in
    the inventory and in ``review.needs_attention``; no name is invented here,
    because a made-up document type is worse than an honest blank.
    """
    from services.claim_rules import document_title

    if not pages:
        return []

    digest = _build_page_digest(pages)
    returned: list[dict[str, Any]] = []
    error: str | None = None
    try:
        returned = _call_model(config, digest, client=client)
    except Exception as exc:  # noqa: BLE001 - a name must not break a run
        error = f"{type(exc).__name__}: {exc}"

    by_page: dict[str, dict[str, Any]] = {}
    for item in returned:
        if not isinstance(item, Mapping):
            continue
        key = _normalize_page_key(item.get("page"), pages)
        if key is None or key in by_page:
            continue
        by_page[key] = {
            "document_type": _clean_document_type(item.get("document_type")),
            "category": _clean_category(item.get("category")),
            "title": item.get("title"),
        }

    inventory: list[dict[str, Any]] = []
    for page in pages:
        key = str(page.get("page_number"))
        raw_text = page.get("raw_text")
        resolved = by_page.get(key)
        document_type = resolved["document_type"] if resolved else None
        inventory.append({
            "page": page.get("page_number"),
            # The model's own words for what the page is; never rewritten.
            "document_type": document_type,
            "category": resolved["category"] if resolved else None,
            "label": (resolved["title"] if resolved else None)
            or document_title(raw_text),
            "source": "model" if document_type else "not_named",
        })

    if error:
        for entry in inventory:
            entry["source"] = "not_named"
        inventory[0]["classification_note"] = (
            f"Klasifikasi dokumen gagal: {error}"
        )
    return inventory


__all__ = [
    "DOCUMENT_CATEGORIES",
    "PageClassification",
    "PageInventory",
    "build_page_classification_prompt",
    "classify_pages",
]

