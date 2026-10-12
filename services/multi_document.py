from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_PAGE_MARKER_PATTERNS = (
    re.compile(
        r"<!--\s*page(?:[ _-]?number)?\s*[:=#-]?\s*(\d+)\s*-->",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(\s*(?:#{1,6}\s*)?(?:page|halaman)\s*(?:number|no\.?)?\s*[:=#-]?\s*)(\d+)(\s*)$",
        re.IGNORECASE | re.MULTILINE,
    ),
)


def merge_document_markdown(
    documents: Sequence[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Join document Markdown while assigning globally unique page markers.

    Each input mapping accepts ``filename``, ``markdown``, and optional
    ``pages`` containing the parser's per-page OCR inventory. If Markdown has
    no page markers, one synthetic marker identifies the source document.
    """
    sections: list[str] = []
    merged_pages: list[dict[str, Any]] = []
    next_page = 1

    for document in documents:
        filename = Path(str(document.get("filename") or "document")).name
        markdown = str(document.get("markdown") or "")
        source_pages = list(document.get("pages") or [])
        if not source_pages:
            source_pages = [{"page_number": 1, "raw_text": markdown}]

        first_global_page = next_page
        page_map: dict[str, int] = {}
        for source_page in source_pages:
            original_page = str(source_page.get("page_number") or len(page_map) + 1)
            page_map[original_page] = next_page
            merged_pages.append({
                **dict(source_page),
                "page_number": next_page,
                "source_file": filename,
                "source_page": source_page.get("page_number"),
            })
            next_page += 1

        marker_matches = _page_marker_matches(markdown)
        if marker_matches:
            replacements: list[tuple[int, int, str]] = []
            for marker_number, start, end in marker_matches:
                global_page = page_map.get(marker_number)
                if global_page is None:
                    try:
                        global_page = first_global_page + int(marker_number) - 1
                    except ValueError:
                        global_page = first_global_page
                replacements.append((start, end, str(global_page)))
            for start, end, number in reversed(replacements):
                markdown = markdown[:start] + number + markdown[end:]
            next_page = max(
                next_page,
                *(int(number) + 1 for _, _, number in replacements),
            )
        else:
            markdown = f"<!-- page: {first_global_page} -->\n\n{markdown}"

        sections.append(
            f"## Source document: {filename}\n\n{markdown.strip()}"
        )

    return "\n\n---\n\n".join(sections), merged_pages


def _page_marker_matches(markdown: str) -> list[tuple[str, int, int]]:
    matches: list[tuple[str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in _PAGE_MARKER_PATTERNS:
        for match in pattern.finditer(markdown):
            number_group = 1 if pattern is _PAGE_MARKER_PATTERNS[0] else 2
            span = match.span(number_group)
            if any(span[0] < end and span[1] > start for start, end in occupied):
                continue
            matches.append((match.group(number_group), span[0], span[1]))
            occupied.append(span)
    return sorted(matches, key=lambda item: item[1])


__all__ = ["merge_document_markdown"]
