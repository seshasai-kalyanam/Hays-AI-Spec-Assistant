"""Split large PDFs into Gemini-compatible parts."""

from __future__ import annotations

import io
from pathlib import Path

import pypdf

# Stay under Gemini's documented 50 MB per-PDF generateContent limit.
GEMINI_PDF_PART_MAX_BYTES = 45 * 1024 * 1024

GEMINI_PDF_TOO_LARGE_MESSAGE = (
    "Your uploaded PDF(s) may be too large for Gemini to process. "
    "Try limiting the total specification to around **600 pages**, "
    "or upload a smaller PDF with only the sections you need."
)


def is_gemini_pdf_size_error(exc: Exception | str) -> bool:
    """True when a Gemini API error likely means the PDF set is too large."""
    err = str(exc).lower()
    if "response_schema" in err or "additional_properties" in err:
        return False
    size_hints = (
        "invalid_argument",
        "too large",
        "too many pages",
        "page limit",
        "exceeds the supported",
        "bytes are too large",
        "total referenced files",
        "document contains",
        "exceeds the supported page limit",
    )
    return any(hint in err for hint in size_hints)


def _gemini_error_text(exc: Exception | str) -> str:
    return str(exc).strip()


def format_gemini_upload_error(exc: Exception | str) -> str:
    """User-facing message when a PDF upload to Gemini fails."""
    err = _gemini_error_text(exc)
    err_lower = err.lower()

    if is_gemini_pdf_size_error(exc):
        return GEMINI_PDF_TOO_LARGE_MESSAGE

    if (
        "resource_exhausted" in err_lower
        or "prepayment credits" in err_lower
        or ("429" in err and ("quota" in err_lower or "billing" in err_lower or "credit" in err_lower))
    ):
        return (
            "Gemini API billing: prepayment credits are depleted or the quota is exhausted. "
            "Add credits in [Google AI Studio](https://ai.studio/projects) and try again."
        )

    if "gemini_api_key" in err_lower or (
        "api key" in err_lower and ("invalid" in err_lower or "not found" in err_lower)
    ):
        return (
            "Gemini API key is missing or invalid. "
            "Check **GEMINI_API_KEY** in `.streamlit/secrets.toml`."
        )

    if "permission_denied" in err_lower or "403" in err:
        return (
            "Gemini API access was denied for this API key. "
            "Verify the key and project permissions in [Google AI Studio](https://aistudio.google.com/apikey)."
        )

    if isinstance(exc, TimeoutError) or "timed out waiting for gemini file" in err_lower:
        return "Gemini took too long to process the PDF. Wait a few minutes and try again."

    if "gemini file failed to process" in err_lower:
        return (
            "Gemini could not process this PDF. "
            "The file may be corrupted, password-protected, or in an unsupported format."
        )

    if "pdf has no pages" in err_lower:
        return "The PDF has no readable pages."

    return f"Upload failed: {err[:500]}"


def format_gemini_pdf_user_error(exc: Exception | str) -> str:
    """User-facing message for Gemini failures; suggests smaller PDFs when appropriate."""
    if is_gemini_pdf_size_error(exc):
        return GEMINI_PDF_TOO_LARGE_MESSAGE
    err = _gemini_error_text(exc)
    return f"An error occurred: {err[:500]}"


def _chunk_byte_size(reader: pypdf.PdfReader, start: int, end: int) -> int:
    writer = pypdf.PdfWriter()
    for page in reader.pages[start:end]:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return len(buf.getvalue())


def split_pdf_for_gemini(
    src: Path,
    dest_dir: Path,
    *,
    max_bytes: int = GEMINI_PDF_PART_MAX_BYTES,
) -> list[Path]:
    """Return one or more PDF paths, each within Gemini's per-file size limit."""
    src = Path(src)
    if src.stat().st_size <= max_bytes:
        return [src]

    dest_dir.mkdir(parents=True, exist_ok=True)
    reader = pypdf.PdfReader(str(src))
    total_pages = len(reader.pages)
    if total_pages == 0:
        raise ValueError(f"PDF has no pages: {src}")

    pages_per_chunk = total_pages
    while pages_per_chunk >= 1:
        fits = True
        for start in range(0, total_pages, pages_per_chunk):
            end = min(start + pages_per_chunk, total_pages)
            if _chunk_byte_size(reader, start, end) > max_bytes:
                fits = False
                break
        if fits:
            break
        pages_per_chunk = max(1, pages_per_chunk // 2)

    paths: list[Path] = []
    part_idx = 0
    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        writer = pypdf.PdfWriter()
        for page in reader.pages[start:end]:
            writer.add_page(page)
        out = dest_dir / f"{src.stem}.part{part_idx:03d}.pdf"
        with open(out, "wb") as f:
            writer.write(f)
        paths.append(out)
        part_idx += 1
    return paths
