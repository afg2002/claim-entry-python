from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from config import AppConfig
from services.claim_form import (
    apply_document_claim_type,
    build_claim_form,
    build_full_claim_json,
)
from services.page_classifier import classify_pages
from services.usage_tracker import (
    combine_usage_reports,
    unavailable_usage_report,
)

LogCallback = Callable[[str, dict[str, Any]], None]


def parse_mineru(
    file_path: str | Path,
    output_dir: str | Path,
    backend: str = "pipeline",
    timeout: int = 3600,
    log_callback: LogCallback | None = None,
    *,
    information_extraction: bool = True,
    llm_config: AppConfig | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if backend.strip().lower() != "pipeline":
        raise ValueError(
            "MinerU adapter hanya mengizinkan backend lokal 'pipeline'; "
            "VLM/API MinerU tidak digunakan."
        )
    source = Path(file_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    output_lines: list[str] = []
    process: subprocess.Popen | None = None

    try:
        config = llm_config or AppConfig.from_file()
        executable = _find_mineru()
        command = [executable, "-p", str(source), "-o", str(output), "-b", backend]
        _emit(log_callback, f"Command: {subprocess.list2cmdline(command)}", started, "starting")

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=_subprocess_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if process.stdout is None:
            raise RuntimeError("MinerU stdout tidak tersedia.")

        lines: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_read_output, args=(process.stdout, lines), daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        last_heartbeat = time.monotonic()

        while True:
            if time.monotonic() > deadline:
                _terminate(process)
                raise TimeoutError(f"MinerU melewati timeout {timeout} detik.")
            try:
                line = lines.get(timeout=0.25)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                if time.monotonic() - last_heartbeat >= 2:
                    _emit(log_callback, "", started, "processing")
                    last_heartbeat = time.monotonic()
                continue

            if line is None:
                if process.poll() is not None:
                    break
                continue

            clean = line.rstrip("\r\n")
            if clean:
                output_lines.append(clean)
                if log_callback:
                    log_callback(clean, _extract_progress(clean, time.perf_counter() - started))
                last_heartbeat = time.monotonic()

        return_code = process.wait(timeout=30)
        reader.join(timeout=5)
        stdout_text = "\n".join(output_lines)
        if return_code != 0:
            raise RuntimeError(f"MinerU exited with code {return_code}:\n" + "\n".join(output_lines[-80:]))

        markdown_path = _best_artifact(output, source.stem, {".md", ".markdown"})
        json_path = _best_artifact(output, source.stem, {".json"})
        markdown = markdown_path.read_text(encoding="utf-8", errors="replace") if markdown_path else ""
        raw_json_data: Any = {}
        if json_path:
            try:
                raw_json_data = json.loads(json_path.read_text(encoding="utf-8", errors="replace"))
            except json.JSONDecodeError:
                raw_json_data = {"raw_json_path": str(json_path), "warning": "Artifact bukan JSON standar."}
        if not markdown_path and not json_path:
            raise FileNotFoundError(f"MinerU selesai tetapi Markdown/JSON tidak ditemukan di {output}")

        extraction: dict[str, Any] | None = None
        extraction_debug: dict[str, Any] = {}
        usage_extraction: dict[str, Any] = {}
        if information_extraction:
            if not markdown.strip():
                raise ValueError(
                    "MinerU menghasilkan JSON tetapi tidak menghasilkan Markdown; "
                    "LangExtract membutuhkan Markdown lengkap."
                )
            from services.langextract_service import extract_claim_from_markdown

            langextract_result = extract_claim_from_markdown(
                markdown=markdown,
                config=config,
                output_dir=output,
                output_stem=source.stem,
            )
            extraction = langextract_result["data"]
            extraction["pages_analyzed"] = 1
            extraction["extraction_engine"] = "langextract"
            extraction["source_stage"] = "mineru_local_markdown"
            extraction["document_conversion_engine"] = "mineru_pipeline"
            extraction["pages_sent_to_remote_vlm"] = []
            extraction["model"] = langextract_result.get("model", config.langextract_model)
            extraction["usage"] = langextract_result.get("usage", {})
            extraction["cost"] = langextract_result.get("cost", {})
            extraction_debug = langextract_result.get("debug_prompts", {})
            usage_extraction = langextract_result.get("usage", {})

        # MinerU never calls a remote VLM, so the VLM stage is reported as not
        # applicable instead of a row of zeros that looks like free inference.
        usage_vlm = unavailable_usage_report(
            stage="vlm_ocr",
            note="MinerU pipeline lokal tidak memakai VLM/API remote.",
        )
        usage_total = combine_usage_reports(
            [usage_vlm, usage_extraction], stage="document_total"
        )
        usage = {
            "vlm_ocr": usage_vlm,
            "extraction": usage_extraction,
            "total": usage_total,
        }

        raw_ocr_pages = [
            {
                "page_number": "document",
                "raw_text": markdown,
                "errors": [],
            }
        ]
        # MinerU emits a single Markdown block rather than a per-page OCR tree,
        # so the document inventory degrades to one page.
        claim_form = None
        page_classifications = (
            classify_pages(raw_ocr_pages, config)
            if raw_ocr_pages and information_extraction
            else []
        )
        if isinstance(extraction, dict) and extraction.get("claim_data"):
            extraction = apply_document_claim_type(
                extraction, raw_ocr_pages, page_classifications
            )
        grouped_form = None
        if isinstance(extraction, dict) and extraction.get("claim_data"):
            claim_form = build_claim_form(
                extraction,
                pages=raw_ocr_pages,
                classifications=page_classifications,
            )
            grouped_form = build_full_claim_json(
                {**extraction, "usage": usage},
                pages=raw_ocr_pages,
                classifications=page_classifications,
            )
        full_json_path = output / f"{source.stem}.full.json"
        full_payload = (
            grouped_form
            if isinstance(grouped_form, dict) and grouped_form
            else extraction
        )
        if full_payload:
            full_json_path.write_text(
                json.dumps(full_payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

        json_data: dict[str, Any] = {
            "claim_form": claim_form,
            "grouped_claim_form": grouped_form,
            "mineru_raw": raw_json_data,
            "information_extraction": extraction,
            "raw_ocr_pages": raw_ocr_pages,
            "debug_prompts": {
                "ocr": {
                    "source": "MinerU local pipeline",
                    "engine": "mineru_pipeline",
                    "model": "MinerU local pipeline (no remote VLM API)",
                    "prompt": None,
                    "request_url": None,
                },
                "extraction": extraction_debug,
            },
            "usage": usage,
        }

        elapsed = round(time.perf_counter() - started, 3)
        if log_callback:
            log_callback("MinerU selesai.", {"current_page": None, "total_pages": None, "percent": 100.0,
                                             "elapsed_seconds": elapsed, "stage": "completed"})
        return _success(
            source, output, markdown, json_data, markdown_path, json_path, elapsed,
            stdout_text, usage, claim_form, full_json_path, grouped_form,
        )
    except Exception as exc:
        if process is not None and process.poll() is None:
            _terminate(process)
        elapsed = round(time.perf_counter() - started, 3)
        error = f"{type(exc).__name__}: {exc}"
        if log_callback:
            log_callback(error, {"current_page": None, "total_pages": None, "percent": None,
                                 "elapsed_seconds": elapsed, "stage": "error"})
        return {"parser": "MinerU", "status": "error", "input_file": source.name, "markdown": "",
                "json_data": {}, "artifacts": {"markdown_path": None, "json_path": None,
                "output_directory": str(output)}, "performance": {"processing_seconds": elapsed},
                "stdout": "\n".join(output_lines), "stderr": "", "error": error}


def _success(source, output, markdown, json_data, md_path, json_path, elapsed, stdout,
             usage=None, claim_form=None, full_json_path=None, grouped_form=None):
    return {"parser": "MinerU", "status": "success", "input_file": source.name, "markdown": markdown,
            "json_data": json_data, "usage": usage or {}, "claim_form": claim_form,
            "grouped_claim_form": grouped_form,
            "artifacts": {"markdown_path": str(md_path) if md_path else None,
            "json_path": str(json_path) if json_path else None,
            "full_json_path": str(full_json_path) if full_json_path else None,
            "output_directory": str(output)},
            "performance": {"processing_seconds": elapsed}, "stdout": stdout, "stderr": "", "error": None}


def _read_output(stdout, lines: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stdout.readline, ""):
            lines.put(line)
    finally:
        stdout.close()
        lines.put(None)


def _extract_progress(line: str, elapsed: float) -> dict[str, Any]:
    current = total = None
    patterns = [r"(?i)\bpage\s*[:#-]?\s*(\d+)\s*/\s*(\d+)",
                r"(?i)\bpages?\s*[:#-]?\s*(\d+)\s+of\s+(\d+)",
                r"(?i)processing\s+page\s+(\d+)\s+(?:of|/)\s*(\d+)",
                r"(?i)\b(\d+)\s*/\s*(\d+)\s*pages?\b"]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            break
    percent = min(100.0, max(0.0, current / total * 100)) if current is not None and total else None
    return {"current_page": current, "total_pages": total, "percent": percent,
            "elapsed_seconds": round(elapsed, 1), "stage": _detect_stage(line)}


def _detect_stage(line: str) -> str:
    text = line.lower()
    if any(x in text for x in ("download", "snapshot", "model source")): return "downloading"
    if any(x in text for x in ("load model", "loading model", "initializ")): return "loading-model"
    if "ocr" in text or "recognition" in text: return "ocr"
    if "table" in text: return "table-recognition"
    if "formula" in text or "latex" in text: return "formula-recognition"
    if any(x in text for x in ("markdown", "writing", "saving", "export")): return "exporting"
    if "error" in text or "failed" in text: return "error"
    return "processing"


def _emit(callback, line: str, started: float, stage: str) -> None:
    if callback:
        callback(line, {"current_page": None, "total_pages": None, "percent": None,
                        "elapsed_seconds": round(time.perf_counter() - started, 1), "stage": stage})


def _subprocess_environment() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _terminate(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill()
    except Exception:
        process.kill()


def _find_mineru() -> str:
    found = shutil.which("mineru")
    if found: return found
    candidates = []
    if os.environ.get("VIRTUAL_ENV"):
        candidates.append(Path(os.environ["VIRTUAL_ENV"]) / "Scripts" / "mineru.exe")
    candidates += [Path(sys.executable).parent / "Scripts" / "mineru.exe",
                   Path(sys.executable).parent / "mineru.exe",
                   Path(sys.executable).parent / "mineru"]
    for candidate in candidates:
        if candidate.exists(): return str(candidate)
    raise FileNotFoundError("MinerU CLI tidak ditemukan. Aktifkan venv dan install mineru[all].")


def _best_artifact(root: Path, stem: str, suffixes: set[str]) -> Path | None:
    candidates = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in suffixes]
    if not candidates: return None
    candidates.sort(key=lambda p: (stem.lower() not in p.stem.lower(), -p.stat().st_size, len(str(p))))
    return candidates[0]
