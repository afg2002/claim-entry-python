from __future__ import annotations

import re
import uuid

from pathlib import Path


PARSER_FORMAT_SUPPORT = {
    "Docling": {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "png",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "bmp",
        "webp",
        "txt",
        "md",
        "html",
        "htm",
        "csv",
        "epub",
        "odt",
        "ods",
        "odp",
        "eml",
        "msg",
        "xml",
    },

    "MinerU": {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "png",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "bmp",
        "webp",
    },

    "Marker": {
        "pdf",
    },

    "OpenDataLoader": {
    "pdf",
    "doc", "docx", "rtf",
    "xls", "xlsx", "csv",
    "ppt", "pptx",
    "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp",
    },
}


SUPPORTED_UPLOAD_TYPES = sorted({
    extension
    for extensions in PARSER_FORMAT_SUPPORT.values()
    for extension in extensions
})


IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "tif",
    "tiff",
    "bmp",
    "webp",
}


def get_file_extension(filename: str) -> str:
    """
    Mengembalikan ekstensi file tanpa titik.

    Contoh:
        report.PDF -> pdf
        document.docx -> docx
    """

    return (
        Path(filename)
        .suffix
        .lower()
        .lstrip(".")
    )


def parser_supports_file(
    parser_name: str,
    filename: str,
) -> bool:
    """
    Memeriksa apakah parser mendukung format file.
    """

    extension = get_file_extension(filename)

    supported_extensions = PARSER_FORMAT_SUPPORT.get(
        parser_name,
        set(),
    )

    return extension in supported_extensions


def get_compatible_parsers(
    filename: str,
) -> list:
    """
    Mengembalikan daftar parser yang kompatibel.
    """

    return [
        parser_name
        for parser_name in PARSER_FORMAT_SUPPORT
        if parser_supports_file(
            parser_name,
            filename,
        )
    ]


def get_incompatible_parsers(
    filename: str,
) -> list:
    """
    Mengembalikan daftar parser yang tidak kompatibel.
    """

    compatible_parsers = set(
        get_compatible_parsers(filename)
    )

    return [
        parser_name
        for parser_name in PARSER_FORMAT_SUPPORT
        if parser_name not in compatible_parsers
    ]


def create_safe_internal_filename(
    original_filename: str,
) -> str:
    """
    Membuat nama file internal yang pendek dan aman untuk Windows,
    tetapi tetap mempertahankan ekstensi asli.

    Contoh:
        Timesheet Afghan signed 2 .pdf
        menjadi:
        doc_a12bc34d.pdf
    """

    suffix = Path(
        original_filename
    ).suffix.lower()

    if not suffix:
        raise ValueError(
            "File tidak memiliki ekstensi."
        )

    unique_id = uuid.uuid4().hex[:8]

    return f"doc_{unique_id}{suffix}"


def sanitize_filename(
    filename: str,
    max_length: int = 50,
) -> str:
    """
    Alternatif jika ingin mempertahankan sebagian nama asli.
    """

    path = Path(filename)

    clean_stem = path.stem.strip(" .")

    clean_stem = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        clean_stem,
    )

    clean_stem = clean_stem.strip(
        " ._-"
    )

    if not clean_stem:
        clean_stem = "document"

    clean_stem = clean_stem[:max_length]

    return f"{clean_stem}{path.suffix.lower()}"


def is_image_file(filename: str) -> bool:
    """
    Memeriksa apakah file dapat ditampilkan dengan st.image().
    """

    return (
        get_file_extension(filename)
        in IMAGE_EXTENSIONS
    )