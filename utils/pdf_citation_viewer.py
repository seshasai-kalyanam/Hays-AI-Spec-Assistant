"""Citation-aware PDF page viewer helpers for the Streamlit app."""
from __future__ import annotations

import re
from dataclasses import dataclass

import streamlit as st
from streamlit_pdf_viewer import pdf_viewer

# Bounded height enables the component's internal scroll area (needed when zoomed).
PDF_VIEWER_HEIGHT_PX = 600


@dataclass(frozen=True)
class SourceCitation:
    """A source line with the PDF pages it points at."""

    line: str
    page: int
    page_end: int = 0

    def source_pages(self) -> list[int]:
        """One-based pages, capped so a wide range does not render the whole PDF."""
        end = self.page_end or self.page
        end = max(self.page, min(end, self.page + 3))
        return list(range(self.page, end + 1))


_SOURCE_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]\s+")
_SOURCE_LINE_RE = re.compile(
    r"^(?P<line>Source\s+\d+\s*:\s*.*?\bPages?\s+(?P<start>\d+)"
    r"(?:\s*[-–—]\s*(?P<end>\d+))?\b.*)$",
    re.IGNORECASE,
)
_SECTION_NUMBER_RE = re.compile(r"\b\d{2}(?:\s+\d{2}){1,3}\b")
_CONTENT_PHRASE_RE = re.compile(
    r"\b[A-Z][\w&.'/-]*(?:\s+[A-Z0-9][\w&.'/-]*){1,5}"
)


def _normalize_source_line(line: str) -> str:
    """Strip markdown list markers so ``Source N:`` lines are recognized."""
    stripped = (line or "").strip()
    return _SOURCE_BULLET_PREFIX_RE.sub("", stripped, count=1).strip()


def _match_source_line(line: str) -> re.Match[str] | None:
    normalized = _normalize_source_line(line)
    if not normalized:
        return None
    return _SOURCE_LINE_RE.match(normalized)


def _citation_from_match(match: re.Match[str]) -> SourceCitation:
    start = int(match.group("start"))
    end_raw = match.group("end")
    end = int(end_raw) if end_raw else start
    return SourceCitation(line=match.group("line").strip(), page=start, page_end=end)


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Return the number of pages in a PDF byte stream."""
    matches = re.findall(rb"/Type\s*/Page\b", pdf_bytes)
    return len(matches) if matches else 1


def extract_source_citations(text: str) -> list[SourceCitation]:
    """Extract source citation lines with page numbers from assistant text."""
    citations: list[SourceCitation] = []
    for line in (text or "").splitlines():
        match = _match_source_line(line)
        if not match:
            continue
        try:
            citations.append(_citation_from_match(match))
        except ValueError:
            continue
    return citations


def build_pdf_filename_citation_suffix(pdf_names: list[str]) -> str:
    """Suffix instructing verbatim PDF filenames when citing sections/pages."""
    names = [name.strip() for name in (pdf_names or []) if (name or "").strip()]
    if not names:
        return ""

    if len(names) == 1:
        filename = names[0]
        return (
            " When you cite specification sections or pages, include the uploaded PDF "
            f"filename verbatim with each reference. "
            f"Uploaded PDF: {filename}. "
            f"Example: Section 26 05 19, Page 15, {filename}."
        )

    listed = "; ".join(names)
    first, second = names[0], names[1]
    return (
        " When you cite specification sections or pages, include the uploaded PDF "
        "filename verbatim with each reference. "
        "Multiple PDFs are attached — pair each section/page citation with the PDF "
        "that contains that reference; different citations in the same reply may use "
        f"different filenames. Uploaded PDFs: {listed}. "
        f"Example: Section 26 05 19, Page 15, {first}; "
        f"Section 23 05 00, Page 4, {second}."
    )


def parse_pdf_name_from_source_line(line: str, pdf_names: list[str]) -> str | None:
    """Return the loaded PDF name if the source line ends with one verbatim."""
    stripped = (line or "").strip()
    if not stripped or not pdf_names:
        return None
    for name in sorted(pdf_names, key=len, reverse=True):
        candidate = (name or "").strip()
        if not candidate:
            continue
        if stripped.endswith(candidate):
            return candidate
    return None


def _select_default_pdf_name() -> str | None:
    names = st.session_state.get("loaded_pdf_names") or []
    return names[0] if names else None


def _slider_key_for_pdf(filename: str, pdf_bytes: bytes) -> str:
    return f"citation_pdf_page_{filename}_{len(pdf_bytes)}"


def _zoom_key_for_pdf(filename: str, pdf_bytes: bytes) -> str:
    return f"pdf_zoom_{filename}_{len(pdf_bytes)}"


def _nav_input_key_for_pdf(filename: str, pdf_bytes: bytes) -> str:
    return f"pdf_page_nav_{filename}_{len(pdf_bytes)}"


def _set_citation_page(
    slider_key: str, nav_key: str, page: int, page_count: int
) -> None:
    bounded = min(max(1, int(page)), page_count)
    st.session_state[slider_key] = bounded
    st.session_state[nav_key] = bounded
    st.session_state.selected_citation_page = bounded


def _go_to_prev_page(slider_key: str, nav_key: str, page_count: int) -> None:
    current = int(st.session_state.get(slider_key, 1))
    _set_citation_page(slider_key, nav_key, current - 1, page_count)


def _go_to_next_page(slider_key: str, nav_key: str, page_count: int) -> None:
    current = int(st.session_state.get(slider_key, 1))
    _set_citation_page(slider_key, nav_key, current + 1, page_count)


def _on_page_nav_input(slider_key: str, nav_key: str, page_count: int) -> None:
    _set_citation_page(slider_key, nav_key, st.session_state[nav_key], page_count)


def _sync_nav_from_slider(slider_key: str, nav_key: str) -> None:
    st.session_state[nav_key] = int(st.session_state[slider_key])


def _render_page_nav(slider_key: str, nav_key: str, page_count: int) -> None:
    """Compact page field with prev/next arrows (toolbar-style)."""
    current = int(st.session_state[slider_key])
    label_col, input_col, total_col, prev_col, next_col = st.columns(
        [0.55, 0.65, 0.55, 0.35, 0.35],
        gap="xxsmall",
        vertical_alignment="center",
    )
    with label_col:
        st.markdown("Page:")
    with input_col:
        st.number_input(
            "Page number",
            min_value=1,
            max_value=page_count,
            step=1,
            key=nav_key,
            label_visibility="collapsed",
            on_change=_on_page_nav_input,
            args=(slider_key, nav_key, page_count),
        )
    with total_col:
        st.markdown(f"/ {page_count}")
    with prev_col:
        st.button(
            "",
            icon=":material/arrow_back:",
            key=f"{nav_key}_prev",
            disabled=current <= 1,
            on_click=_go_to_prev_page,
            args=(slider_key, nav_key, page_count),
            help="Previous page",
        )
    with next_col:
        st.button(
            "",
            icon=":material/arrow_forward:",
            key=f"{nav_key}_next",
            disabled=current >= page_count,
            on_click=_go_to_next_page,
            args=(slider_key, nav_key, page_count),
            help="Next page",
        )


def _highlight_phrases(answer_text: str, citation: SourceCitation) -> list[str]:
    """Section number plus a few proper names from the answer, for PDF highlights."""
    phrases: list[str] = []
    section = _SECTION_NUMBER_RE.search(citation.line or "")
    if section:
        phrases.append(section.group(0))
    body = re.split(r"(?im)^\s*\**sources\**\s*$", answer_text or "", maxsplit=1)[0]
    for phrase in _CONTENT_PHRASE_RE.findall(body):
        cleaned = phrase.strip(" .")
        if len(cleaned) < 8 or cleaned in phrases:
            continue
        phrases.append(cleaned)
        if len(phrases) >= 4:
            break
    return phrases


def _highlight_annotations(
    pdf_bytes: bytes, pages: list[int], phrases: list[str]
) -> list[dict]:
    """Yellow boxes over matching text. Coordinates use the top-left of the page."""
    if not pdf_bytes or not pages or not phrases:
        return []
    try:
        import pypdfium2 as pdfium
    except Exception:
        return []

    annotations: list[dict] = []
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        for page_number in pages:
            if page_number < 1 or page_number > len(pdf) or len(annotations) >= 12:
                continue
            page = pdf[page_number - 1]
            page_height = float(page.get_height())
            textpage = page.get_textpage()
            try:
                for phrase in phrases:
                    if len(annotations) >= 12:
                        break
                    searcher = textpage.search(phrase, match_case=False)
                    hits = 0
                    try:
                        while hits < 2 and len(annotations) < 12:
                            found = searcher.get_next()
                            if not found:
                                break
                            start, count = found
                            for rect_index in range(textpage.count_rects(start, count)):
                                left, bottom, right, top = textpage.get_rect(rect_index)
                                annotations.append(
                                    {
                                        "page": page_number,
                                        "x": round(float(left), 2),
                                        "y": round(page_height - float(top), 2),
                                        "width": round(max(float(right) - float(left), 4.0), 2),
                                        "height": round(max(float(top) - float(bottom), 4.0), 2),
                                        "color": "yellow",
                                    }
                                )
                                hits += 1
                                if hits >= 2 or len(annotations) >= 12:
                                    break
                    finally:
                        searcher.close()
            finally:
                textpage.close()
                page.close()
    except Exception:
        return annotations
    finally:
        pdf.close()
    return annotations


def _open_source(pages: list[int], pdf_name: str | None, phrases: list[str]) -> None:
    """Show the cited pages in the viewer and remember what to highlight."""
    selected_name = pdf_name or _select_default_pdf_name()
    if not selected_name or not pages:
        return
    first_page = max(1, int(pages[0]))
    st.session_state.selected_citation_pdf = selected_name
    st.session_state.selected_citation_page = first_page
    st.session_state.selected_citation_pages = [max(1, int(page)) for page in pages]
    st.session_state.citation_highlight_phrases = list(phrases or [])
    files_by_upload = st.session_state.get("pdf_viewer_files_by_upload") or {}
    pdf_bytes = files_by_upload.get(selected_name)
    if pdf_bytes is None:
        return
    page_count = count_pdf_pages(pdf_bytes)
    bounded = min(first_page, page_count)
    st.session_state.selected_citation_pages = [
        page for page in st.session_state.selected_citation_pages if page <= page_count
    ] or [bounded]
    slider_key = _slider_key_for_pdf(selected_name, pdf_bytes)
    nav_key = _nav_input_key_for_pdf(selected_name, pdf_bytes)
    st.session_state[slider_key] = bounded
    st.session_state[nav_key] = bounded


def _browse_single_page(slider_key: str) -> None:
    """Manual page changes leave the cited range and show one page."""
    st.session_state.selected_citation_pages = [int(st.session_state[slider_key])]
    st.session_state.citation_highlight_phrases = []
    st.session_state.selected_citation_page = int(st.session_state[slider_key])


def _split_response_segments(
    text: str,
) -> list[tuple[str, str | list[tuple[int, SourceCitation]]]]:
    """Split assistant text into markdown blocks and consecutive source-citation runs."""
    segments: list[tuple[str, str | list[tuple[int, SourceCitation]]]] = []
    markdown_buffer: list[str] = []
    citation_buffer: list[tuple[int, SourceCitation]] = []

    def flush_markdown() -> None:
        if markdown_buffer:
            segments.append(("markdown", "\n".join(markdown_buffer)))
            markdown_buffer.clear()

    def flush_citations() -> None:
        if citation_buffer:
            segments.append(("citations", list(citation_buffer)))
            citation_buffer.clear()

    for line_index, line in enumerate((text or "").splitlines()):
        if not line.strip():
            # Normalized Sources blocks use blank lines between entries; keep one run.
            if citation_buffer:
                continue
            markdown_buffer.append(line)
            continue

        match = _match_source_line(line)
        if match:
            flush_markdown()
            citation_buffer.append((line_index, _citation_from_match(match)))
            continue
        flush_citations()
        markdown_buffer.append(line)

    flush_markdown()
    flush_citations()
    return segments


def _render_citation_rows(
    citations: list[tuple[int, SourceCitation]],
    message_key: str,
    segment_index: int,
    answer_text: str,
) -> None:
    """Render source lines with a button that opens those PDF pages."""
    loaded_names = [
        name.strip()
        for name in (st.session_state.get("loaded_pdf_names") or [])
        if (name or "").strip()
    ]
    with st.container(
        key=f"hays_citation_sources_{message_key}_{segment_index}",
        gap="xxsmall",
    ):
        for citation_index, (line_index, citation) in enumerate(citations, start=1):
            text_col, action_col = st.columns(
                [4, 1], gap="small", vertical_alignment="center"
            )
            with text_col:
                st.markdown(citation.line)
            with action_col:
                pdf_name = parse_pdf_name_from_source_line(citation.line, loaded_names)
                st.button(
                    "View source",
                    key=f"view_citation_{message_key}_{line_index}_{citation_index}",
                    type="secondary",
                    width="stretch",
                    on_click=_open_source,
                    args=(
                        citation.source_pages(),
                        pdf_name,
                        _highlight_phrases(answer_text, citation),
                    ),
                )


def render_response_with_citation_actions(text: str, message_key: str) -> None:
    """Render assistant markdown and add a View page action beside source lines."""
    citation_segment_index = 0
    for segment_type, content in _split_response_segments(text):
        if segment_type == "markdown":
            st.markdown(content)
            continue
        _render_citation_rows(content, message_key, citation_segment_index, text)
        citation_segment_index += 1


@st.fragment
def render_pdf_page(pdf_bytes: bytes, filename: str, page: int) -> None:
    """Render a single page from a PDF with page nav, sliders, and viewer."""
    page_count = count_pdf_pages(pdf_bytes)
    bounded_page = min(max(1, page), page_count)
    slider_key = _slider_key_for_pdf(filename, pdf_bytes)
    zoom_key = _zoom_key_for_pdf(filename, pdf_bytes)
    nav_key = _nav_input_key_for_pdf(filename, pdf_bytes)
    if slider_key not in st.session_state:
        st.session_state[slider_key] = bounded_page
    if zoom_key not in st.session_state:
        st.session_state[zoom_key] = 100
    if nav_key not in st.session_state:
        st.session_state[nav_key] = st.session_state[slider_key]

    _sync_nav_from_slider(slider_key, nav_key)

    source_pages = [
        page_number
        for page_number in (st.session_state.get("selected_citation_pages") or [bounded_page])
        if 1 <= int(page_number) <= page_count
    ] or [bounded_page]
    st.caption(f"Viewing **{filename}**")
    if len(source_pages) > 1:
        st.caption(f"Source pages {source_pages[0]}–{source_pages[-1]}")
    with st.container(key=f"hays_page_nav_{filename}"):
        _render_page_nav(slider_key, nav_key, page_count)

    page_col, zoom_col = st.columns(2, gap="small")
    with page_col:
        selected_page = st.slider(
            "Page",
            min_value=1,
            max_value=page_count,
            key=slider_key,
            on_change=_browse_single_page,
            args=(slider_key,),
        )
    with zoom_col:
        zoom_percent = st.slider(
            "Zoom",
            min_value=10,
            max_value=300,
            step=10,
            format="%d%%",
            key=zoom_key,
        )

    st.session_state.selected_citation_page = selected_page
    if selected_page not in source_pages:
        source_pages = [selected_page]
    phrases = st.session_state.get("citation_highlight_phrases") or []
    annotations = _highlight_annotations(pdf_bytes, source_pages, phrases)
    pdf_viewer(
        pdf_bytes,
        pages_to_render=source_pages,
        annotations=annotations,
        width="100%",
        height=min(980, 560 * len(source_pages)),
        zoom_level=zoom_percent / 100.0,
        show_page_separator=len(source_pages) > 1,
    )


@st.fragment
def render_citation_pdf_viewer() -> None:
    """Render the selected citation PDF page in the right column."""
    files_by_upload = st.session_state.get("pdf_viewer_files_by_upload") or {}
    loaded_names = [
        name for name in st.session_state.get("loaded_pdf_names") or [] if name in files_by_upload
    ]
    selected_name = st.session_state.get("selected_citation_pdf")
    selected_page = int(st.session_state.get("selected_citation_page") or 1)

    if not loaded_names:
        return

    if selected_name not in loaded_names:
        selected_name = loaded_names[0]
        st.session_state.selected_citation_pdf = selected_name

    with st.container(border=True, gap="xxsmall"):
        # st.subheader("PDF Viewer")
        if len(loaded_names) > 1:
            selected_name = st.selectbox(
                "PDF",
                loaded_names,
                index=loaded_names.index(selected_name),
                key="citation_pdf_selector",
            )
            st.session_state.selected_citation_pdf = selected_name
        render_pdf_page(files_by_upload[selected_name], selected_name, selected_page)
