from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from utils.pdf_converter import convert_to_pdf


def parse_opendataloader(
    file_path: str | Path,
    output_dir: str | Path,
    mode: str = "local",
    hybrid_mode: str = "auto",
    conversion_timeout: int = 300,
    wait_seconds: int = 10,
) -> dict[str, Any]:
    started = time.perf_counter()
    source = Path(file_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    conversion_dir = output / "converted_input"
    conversion_info: dict[str, Any] = {}
    library_result: Any = None

    try:
        import opendataloader_pdf

        conversion_info = convert_to_pdf(
            source,
            conversion_dir,
            timeout=conversion_timeout,
        )
        pdf_source = Path(conversion_info["pdf_path"]).resolve()

        kwargs: dict[str, Any] = {
            "input_path": [str(pdf_source)],
            "output_dir": str(output),
            "format": "markdown,json",
        }
        if mode == "hybrid":
            kwargs["hybrid"] = "docling-fast"
            kwargs["hybrid_mode"] = hybrid_mode

        library_result = opendataloader_pdf.convert(**kwargs)
        generated_files = _wait_for_result_files(output, conversion_dir, wait_seconds)
        markdown_path = _best_artifact(
            output, pdf_source.stem, {".md", ".markdown"}, conversion_dir
        )
        json_path = _best_artifact(
            output, pdf_source.stem, {".json"}, conversion_dir
        )

        markdown = (
            markdown_path.read_text(encoding="utf-8", errors="replace")
            if markdown_path else ""
        )
        json_data: Any = {}
        if json_path:
            try:
                json_data = json.loads(
                    json_path.read_text(encoding="utf-8", errors="replace")
                )
            except json.JSONDecodeError:
                json_data = {
                    "raw_json_path": str(json_path),
                    "warning": "File ditemukan tetapi bukan JSON standar.",
                }

        if not markdown_path and not json_path:
            listing = "\n".join(str(path) for path in generated_files) or "(kosong)"
            raise FileNotFoundError(
                "OpenDataLoader tidak menghasilkan Markdown/JSON.\n"
                f"Original input: {source}\n"
                f"Converted PDF: {pdf_source}\n"
                f"Mode: {mode}; hybrid_mode: {hybrid_mode}\n"
                f"Return value: {library_result!r}\n"
                f"Files: {listing}\n"
                "Jika Hybrid, periksa terminal hybrid server untuk HTTP 500."
            )

        parser_name = (
            f"OpenDataLoader Hybrid ({hybrid_mode})"
            if mode == "hybrid" else "OpenDataLoader Local"
        )
        return {
            "parser": parser_name,
            "status": "success",
            "input_file": source.name,
            "markdown": markdown,
            "json_data": json_data,
            "metadata": {
                "mode": mode,
                "hybrid_backend": "docling-fast" if mode == "hybrid" else None,
                "hybrid_mode": hybrid_mode if mode == "hybrid" else None,
                "original_format": source.suffix.lower(),
                "pdf_conversion": conversion_info,
                "processed_pdf": str(pdf_source),
                "generated_files": [str(path) for path in generated_files],
                "library_result": repr(library_result),
            },
            "artifacts": {
                "markdown_path": str(markdown_path) if markdown_path else None,
                "json_path": str(json_path) if json_path else None,
                "converted_pdf_path": str(pdf_source),
                "output_directory": str(output),
            },
            "performance": {
                "processing_seconds": round(time.perf_counter() - started, 3),
                "pdf_conversion_seconds": conversion_info.get(
                    "processing_seconds", 0
                ),
            },
            "stdout": conversion_info.get("stdout", ""),
            "stderr": conversion_info.get("stderr", ""),
            "error": None,
        }
    except Exception as exc:
        parser_name = (
            f"OpenDataLoader Hybrid ({hybrid_mode})"
            if mode == "hybrid" else "OpenDataLoader Local"
        )
        return {
            "parser": parser_name,
            "status": "error",
            "input_file": source.name,
            "markdown": "",
            "json_data": {},
            "metadata": {
                "mode": mode,
                "original_format": source.suffix.lower(),
                "pdf_conversion": conversion_info,
                "library_result": repr(library_result),
            },
            "artifacts": {
                "markdown_path": None,
                "json_path": None,
                "converted_pdf_path": conversion_info.get("pdf_path"),
                "output_directory": str(output),
            },
            "performance": {
                "processing_seconds": round(time.perf_counter() - started, 3),
                "pdf_conversion_seconds": conversion_info.get(
                    "processing_seconds", 0
                ),
            },
            "stdout": conversion_info.get("stdout", ""),
            "stderr": conversion_info.get("stderr", ""),
            "error": f"{type(exc).__name__}: {exc}",
        }


def _wait_for_result_files(
    output: Path,
    excluded_dir: Path,
    timeout: int,
) -> list[Path]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        files = [path for path in output.rglob("*") if path.is_file()]
        results = [
            path for path in files
            if excluded_dir not in path.parents
            and path.suffix.lower() in {".md", ".markdown", ".json"}
        ]
        if results:
            return files
        time.sleep(0.5)
    return [path for path in output.rglob("*") if path.is_file()]


def _best_artifact(
    root: Path,
    stem: str,
    suffixes: set[str],
    excluded_dir: Path,
) -> Path | None:
    candidates = [
        path for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in suffixes
        and excluded_dir not in path.parents
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda path: (
            stem.lower() not in path.stem.lower(),
            -path.stat().st_size,
            len(str(path)),
        )
    )
    return candidates[0]
