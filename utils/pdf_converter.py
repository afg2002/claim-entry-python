from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"
}
WORD_EXTENSIONS = {".doc", ".docx", ".rtf"}
EXCEL_EXTENSIONS = {".xls", ".xlsx", ".csv"}
POWERPOINT_EXTENSIONS = {".ppt", ".pptx"}
UNSUPPORTED_MEDIA_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".avi", ".mov", ".mkv", ".webm",
}


def can_convert_to_pdf(file_path: str | Path) -> bool:
    suffix = Path(file_path).suffix.lower()
    return (
        suffix == ".pdf"
        or suffix in IMAGE_EXTENSIONS
        or suffix in WORD_EXTENSIONS
        or suffix in EXCEL_EXTENSIONS
        or suffix in POWERPOINT_EXTENSIONS
    )


def convert_to_pdf(
    source_path: str | Path,
    output_dir: str | Path,
    timeout: int = 300,
) -> dict[str, Any]:
    del timeout  # COM automation has no native timeout in this synchronous patch.
    started = time.perf_counter()
    source = Path(source_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        raise FileNotFoundError(f"Input file tidak ditemukan: {source}")

    suffix = source.suffix.lower()
    if suffix in UNSUPPORTED_MEDIA_EXTENSIONS:
        raise ValueError("Audio/video tidak dikonversi ke PDF untuk OpenDataLoader.")

    if suffix == ".pdf":
        return _result(source, "passthrough", False, started)

    destination = output / f"{source.stem}.pdf"

    if suffix in IMAGE_EXTENSIONS:
        _image_to_pdf(source, destination)
        return _result(destination, "pillow-image-to-pdf", True, started)

    if suffix in WORD_EXTENSIONS:
        _word_to_pdf(source, destination)
        return _result(destination, "microsoft-word-com", True, started)

    if suffix in EXCEL_EXTENSIONS:
        _excel_to_pdf(source, destination)
        return _result(destination, "microsoft-excel-com", True, started)

    if suffix in POWERPOINT_EXTENSIONS:
        _powerpoint_to_pdf(source, destination)
        return _result(destination, "microsoft-powerpoint-com", True, started)

    raise ValueError(
        f"Format {suffix or '(tanpa ekstensi)'} tidak didukung untuk "
        "konversi PDF tanpa LibreOffice."
    )


def _image_to_pdf(source: Path, destination: Path) -> None:
    with Image.open(source) as image:
        frames: list[Image.Image] = []
        frame_count = getattr(image, "n_frames", 1)

        for frame_index in range(frame_count):
            if frame_count > 1:
                image.seek(frame_index)

            frame = ImageOps.exif_transpose(image.copy())
            if frame.mode in ("RGBA", "LA"):
                background = Image.new("RGB", frame.size, "white")
                alpha = frame.getchannel("A")
                background.paste(frame.convert("RGB"), mask=alpha)
                frame = background
            elif frame.mode != "RGB":
                frame = frame.convert("RGB")

            frames.append(frame)

        first, *rest = frames
        first.save(
            destination,
            "PDF",
            resolution=150.0,
            save_all=bool(rest),
            append_images=rest,
        )

        for frame in frames:
            frame.close()

    _validate_pdf(destination)


def _word_to_pdf(source: Path, destination: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        document = word.Documents.Open(
            str(source),
            ReadOnly=True,
            AddToRecentFiles=False,
        )
        document.ExportAsFixedFormat(
            OutputFileName=str(destination),
            ExportFormat=17,  # wdExportFormatPDF
            OpenAfterExport=False,
        )
    finally:
        if document is not None:
            document.Close(SaveChanges=False)
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()

    _validate_pdf(destination)


def _excel_to_pdf(source: Path, destination: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    excel = None
    workbook = None
    try:
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        workbook = excel.Workbooks.Open(str(source), ReadOnly=True)
        workbook.ExportAsFixedFormat(0, str(destination))  # xlTypePDF
    finally:
        if workbook is not None:
            workbook.Close(SaveChanges=False)
        if excel is not None:
            excel.Quit()
        pythoncom.CoUninitialize()

    _validate_pdf(destination)


def _powerpoint_to_pdf(source: Path, destination: Path) -> None:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    powerpoint = None
    presentation = None
    try:
        powerpoint = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = powerpoint.Presentations.Open(
            str(source),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        presentation.SaveAs(str(destination), 32)  # ppSaveAsPDF
    finally:
        if presentation is not None:
            presentation.Close()
        if powerpoint is not None:
            powerpoint.Quit()
        pythoncom.CoUninitialize()

    _validate_pdf(destination)


def _validate_pdf(destination: Path) -> None:
    if not destination.exists() or destination.stat().st_size == 0:
        raise FileNotFoundError(
            "Konversi selesai dipanggil tetapi PDF tidak ditemukan: "
            f"{destination}"
        )

    with destination.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError(f"Output bukan PDF valid: {destination}")


def _result(
    pdf_path: Path,
    method: str,
    converted: bool,
    started: float,
) -> dict[str, Any]:
    _validate_pdf(pdf_path)
    return {
        "pdf_path": str(pdf_path),
        "method": method,
        "converted": converted,
        "processing_seconds": round(time.perf_counter() - started, 3),
        "stdout": f"PDF siap diproses: {pdf_path}",
        "stderr": "",
    }
