from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def parse_marker(
    file_path: str | Path,
    output_dir: str | Path,
    mode: str = "fast",
) -> dict[str, Any]:
    started = time.perf_counter()
    source = Path(file_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict

        artifact_dict = create_model_dict()
        converter = PdfConverter(
            artifact_dict=artifact_dict,
            config={"mode": mode},
        )
        markdown_result = converter(str(source))
        markdown = markdown_result.markdown

        json_converter = PdfConverter(
            artifact_dict=artifact_dict,
            renderer="marker.renderers.json.JSONRenderer",
            config={"mode": mode},
        )
        json_result = json_converter(str(source))
        json_data = json_result.model_dump(mode="json")

        markdown_path = output / f"{source.stem}.md"
        json_path = output / f"{source.stem}.json"
        markdown_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        images = getattr(markdown_result, "images", {})
        image_paths: list[str] = []
        for image_name, image in images.items():
            image_path = output / image_name
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(image_path)
            image_paths.append(str(image_path))

        elapsed = round(time.perf_counter() - started, 3)
        return {
            "parser": f"Marker ({mode})",
            "status": "success",
            "input_file": source.name,
            "markdown": markdown,
            "json_data": json_data,
            "metadata": {
                "mode": mode,
                "page_count": getattr(converter, "page_count", None),
                "image_paths": image_paths,
            },
            "artifacts": {
                "markdown_path": str(markdown_path),
                "json_path": str(json_path),
                "output_directory": str(output),
            },
            "performance": {"processing_seconds": elapsed},
            "stdout": "",
            "stderr": "",
            "error": None,
        }
    except Exception as exc:
        return {
            "parser": f"Marker ({mode})",
            "status": "error",
            "input_file": source.name,
            "markdown": "",
            "json_data": {},
            "artifacts": {
                "markdown_path": None,
                "json_path": None,
                "output_directory": str(output),
            },
            "performance": {
                "processing_seconds": round(time.perf_counter() - started, 3)
            },
            "stdout": "",
            "stderr": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
