"""Tools for reading non-code document files.

Provides LangChain tools for reading PDFs and images, with graceful
fallback when optional dependencies are not installed.
"""

import base64
import mimetypes
from pathlib import Path

from langchain_core.tools import tool

_SUPPORTED_IMAGE_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
})


def _parse_page_range(pages: str, total: int) -> tuple[int, int]:
    """Parse a page range string like '1-5' or '3' into zero-based start/end.

    Args:
        pages: A page range string (1-based). Examples: '3', '1-5'.
        total: Total number of pages in the document.

    Returns:
        A tuple of (start, end) as zero-based indices, end exclusive.

    Raises:
        ValueError: If the page range format is invalid or out of bounds.
    """
    pages = pages.strip()
    if "-" in pages:
        parts = pages.split("-", 1)
        try:
            start = int(parts[0]) - 1
            end = int(parts[1])
        except ValueError as exc:
            msg = f"Invalid page range: {pages!r}. Expected format like '1-5' or '3'."
            raise ValueError(msg) from exc
    else:
        try:
            start = int(pages) - 1
            end = int(pages)
        except ValueError as exc:
            msg = f"Invalid page number: {pages!r}. Expected a number like '3'."
            raise ValueError(msg) from exc

    if start < 0 or end > total or start >= end:
        msg = (
            f"Page range {pages!r} is out of bounds. "
            f"Document has {total} pages (1-{total})."
        )
        raise ValueError(msg)

    return start, end


@tool(description="Read text content from a PDF file. Optionally specify a page range like '1-5'.")
def read_pdf(path: str, pages: str | None = None) -> str:
    """Read text content from a PDF file.

    Uses pymupdf (fitz) for high-quality extraction when available,
    otherwise returns a message indicating the dependency is missing.

    Args:
        path: The filesystem path to the PDF file.
        pages: An optional page range string (1-based). Examples: '3', '1-5'.

    Returns:
        The extracted text content from the PDF.
    """
    filepath = Path(path)
    if not filepath.exists():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    if filepath.suffix.lower() != ".pdf":
        msg = f"Not a PDF file: {path}"
        raise ValueError(msg)

    try:
        import fitz  # noqa: PLC0415
    except ImportError:
        return (
            f"Cannot read PDF at {path}: pymupdf is not installed. "
            "Install it with: pip install pymupdf"
        )

    doc = fitz.open(str(filepath))
    try:
        total = len(doc)
        if pages is not None:
            start, end = _parse_page_range(pages, total)
        else:
            start, end = 0, total

        text_parts: list[str] = []
        for i in range(start, end):
            page = doc[i]
            text = page.get_text()
            text_parts.append(f"--- Page {i + 1} ---\n{text}")

        return "\n".join(text_parts) if text_parts else "(No text content found in PDF)"
    finally:
        doc.close()


@tool(description="Read an image file and return its base64 data URI for model consumption.")
def read_image(path: str) -> str:
    """Read an image file and return a base64-encoded data URI.

    The data URI can be passed to multi-modal models for image understanding.
    Supports PNG, JPEG, GIF, WebP, and SVG formats.

    Args:
        path: The filesystem path to the image file.

    Returns:
        A base64 data URI string for the image, or a description placeholder
        if the format is unsupported.
    """
    filepath = Path(path)
    if not filepath.exists():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    mime_type, _ = mimetypes.guess_type(str(filepath))
    if mime_type not in _SUPPORTED_IMAGE_TYPES:
        return (
            f"Unsupported image format: {mime_type or 'unknown'} for {path}. "
            f"Supported formats: {', '.join(sorted(_SUPPORTED_IMAGE_TYPES))}"
        )

    data = filepath.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
