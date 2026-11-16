"""Page-aware, section-aware chunking for construction specification PDFs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import pypdfium2 as pdfium

CHUNK_TARGET_CHARS = 1200
CHUNK_MAX_CHARS = 1800
UNIT_SPLIT_CHARS = 700
EMBED_OVERLAP_CHARS = 200
MIN_PAGE_TEXT_CHARS = 30
SCANNED_PAGE_RATIO = 0.9

_SECTION_NUMBER = r"(\d{2}\s?\d{2}\s?\d{2}(?:\.\d{1,2})?)"
SECTION_HEADING_RE = re.compile(rf"^SECTION\s+{_SECTION_NUMBER}\b\s*[-–—:]?\s*(.*)$")
SECTION_FOOTER_RE = re.compile(rf"^(?:.*?\s)?{_SECTION_NUMBER}\s*[-–—]\s*\d{{1,3}}$")
END_OF_SECTION_RE = re.compile(r"^END\s+OF\s+SECTION\b", re.IGNORECASE)
ARTICLE_RE = re.compile(
    r"^(PART\s+[1-3]\s*[-–—]\s*[A-Z][A-Z ,&/()\-]+|\d{1,2}\.\d{1,2}\s+[A-Z][A-Z0-9 ,&/()\-.]{2,80})$"
)
PARAGRAPH_RE = re.compile(r"^(?:[A-Z]|\d{1,2}|[a-z])\.\s+\S")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+")


class ScannedPdfError(ValueError):
    """Raised when a PDF has no extractable text layer."""


@dataclass
class _Unit:
    section_number: str | None
    section_title: str | None
    article: str | None
    page: int
    text: str


@dataclass
class SpecChunk:
    chunk_index: int
    section_number: str | None
    section_title: str | None
    article: str | None
    page_start: int
    page_end: int
    content: str
    embed_text: str = ""


def _normalize_section_number(raw: str) -> str:
    digits = re.sub(r"\s+", "", raw)
    base, _, suffix = digits.partition(".")
    if len(base) == 6:
        base = f"{base[0:2]} {base[2:4]} {base[4:6]}"
    return f"{base}.{suffix}" if suffix else base


def _clean_page_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(lines)


def _page_text(page: pdfium.PdfPage) -> str:
    textpage = page.get_textpage()
    try:
        if hasattr(textpage, "get_text_bounded"):
            return textpage.get_text_bounded()
        return textpage.get_text_range()
    finally:
        textpage.close()


def extract_pdf_pages(
    pdf_bytes: bytes,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    """Return cleaned text for every page (index 0 = PDF page 1)."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        total = len(pdf)
        if total == 0:
            raise ValueError("The PDF has no pages.")
        pages: list[str] = []
        for i in range(total):
            page = pdf[i]
            try:
                pages.append(_clean_page_text(_page_text(page)))
            finally:
                page.close()
            if on_progress and (i % 25 == 0 or i == total - 1):
                on_progress(i + 1, total)
    finally:
        pdf.close()
    empty = sum(1 for text in pages if len(text.strip()) < MIN_PAGE_TEXT_CHARS)
    if empty / len(pages) >= SCANNED_PAGE_RATIO:
        raise ScannedPdfError(
            "This PDF has no readable text layer (it looks scanned). "
            "Upload a text-based PDF or run OCR on it first."
        )
    return pages


def _footer_section(lines: list[str]) -> str | None:
    for line in lines[-6:] + lines[:4]:
        m = SECTION_FOOTER_RE.match(line)
        if m:
            return _normalize_section_number(m.group(1))
    return None


def _split_long_text(text: str, limit: int) -> list[str]:
    """Split oversized text on sentence boundaries, then hard-wrap if needed."""
    pieces: list[str] = []
    current = ""
    for sentence in SENTENCE_SPLIT_RE.split(text):
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        if current and len(current) + len(sentence) + 1 > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _page_units(
    page_no: int,
    text: str,
    state: dict,
) -> list[_Unit]:
    """Split one page into paragraph-level units, tracking section and article state."""
    lines = [line for line in text.split("\n") if line]
    has_heading = any(SECTION_HEADING_RE.match(line) for line in lines)
    footer = _footer_section(lines)
    if footer and not has_heading and footer != state["section_number"]:
        state["section_number"] = footer
        state["section_title"] = state["titles"].get(footer)
        state["article"] = None

    units: list[_Unit] = []
    block: list[str] = []

    def flush() -> None:
        if block:
            units.append(
                _Unit(
                    state["section_number"],
                    state["section_title"],
                    state["article"],
                    page_no,
                    "\n".join(block),
                )
            )
            block.clear()

    for line in lines:
        if SECTION_FOOTER_RE.match(line):
            continue
        heading = SECTION_HEADING_RE.match(line)
        if heading:
            flush()
            number = _normalize_section_number(heading.group(1))
            title = heading.group(2).strip() or None
            state.update(section_number=number, section_title=title, article=None, need_title=not title)
            if title:
                state["titles"][number] = title
            block.append(line)
            continue
        if state.get("need_title") and line.isupper() and len(line) < 120:
            state["section_title"] = line
            state["titles"][state["section_number"]] = line
            state["need_title"] = False
        if END_OF_SECTION_RE.match(line):
            block.append(line)
            flush()
            continue
        if ARTICLE_RE.match(line):
            flush()
            state["article"] = line
        elif PARAGRAPH_RE.match(line) and sum(len(b) for b in block) > UNIT_SPLIT_CHARS:
            flush()
        block.append(line)
        if sum(len(b) for b in block) > CHUNK_TARGET_CHARS:
            flush()
    flush()
    return units


def _chunk_header(file_name: str, chunk: SpecChunk) -> str:
    parts = [file_name]
    if chunk.section_number:
        parts.append(f"Section {chunk.section_number} {chunk.section_title or ''}".strip())
    if chunk.article:
        parts.append(chunk.article)
    pages = (
        f"PDF page {chunk.page_start}"
        if chunk.page_start == chunk.page_end
        else f"PDF pages {chunk.page_start}-{chunk.page_end}"
    )
    parts.append(pages)
    return " | ".join(parts)


def _emit_chunks(units: list[_Unit]) -> list[SpecChunk]:
    """Pack units into chunks that never cross a section boundary."""
    chunks: list[SpecChunk] = []
    current: list[_Unit] = []

    def emit(group: list[_Unit], content: str) -> None:
        chunks.append(
            SpecChunk(
                chunk_index=len(chunks),
                section_number=group[0].section_number,
                section_title=group[0].section_title,
                article=group[0].article,
                page_start=min(u.page for u in group),
                page_end=max(u.page for u in group),
                content=content,
            )
        )

    def flush() -> None:
        if current:
            emit(list(current), "\n".join(u.text for u in current))
            current.clear()

    for unit in units:
        if len(unit.text) > CHUNK_MAX_CHARS:
            flush()
            for piece in _split_long_text(unit.text, CHUNK_TARGET_CHARS):
                emit([unit], piece)
            continue
        same_section = not current or current[-1].section_number == unit.section_number
        size = sum(len(u.text) for u in current) + len(unit.text)
        if not same_section or size > CHUNK_TARGET_CHARS:
            flush()
        current.append(unit)
    flush()
    return chunks


def build_spec_chunks(pages: list[str], file_name: str) -> list[SpecChunk]:
    """Chunk page texts; each chunk gets a contextual header and neighbor overlap for embedding."""
    state: dict = {
        "section_number": None,
        "section_title": None,
        "article": None,
        "need_title": False,
        "titles": {},
    }
    units: list[_Unit] = []
    for page_no, text in enumerate(pages, start=1):
        units.extend(_page_units(page_no, text, state))
    chunks = _emit_chunks(units)
    for i, chunk in enumerate(chunks):
        if not chunk.section_title and chunk.section_number:
            chunk.section_title = state["titles"].get(chunk.section_number)
        prev_tail = ""
        next_head = ""
        if i > 0 and chunks[i - 1].section_number == chunk.section_number:
            prev_tail = chunks[i - 1].content[-EMBED_OVERLAP_CHARS:]
        if i + 1 < len(chunks) and chunks[i + 1].section_number == chunk.section_number:
            next_head = chunks[i + 1].content[:EMBED_OVERLAP_CHARS]
        body = "\n".join(part for part in (prev_tail, chunk.content, next_head) if part)
        chunk.embed_text = f"{_chunk_header(file_name, chunk)}\n\n{body}"
    return chunks
