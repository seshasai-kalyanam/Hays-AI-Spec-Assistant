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
    """A source line with an extracted one-based PDF page number."""

    line: str
    page: int


_SOURCE_BULLET_PREFIX_RE = re.compile(r"^\s*[-*•]\s+")
_SOURCE_LINE_RE = re.compile(
    r"^(?P<line>Source\s+\d+\s*:\s*.*?\bPage\s+(?P<page>\d+)\b.*)$",
    re.IGNORECASE,
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
            page = int(match.group("page"))
        except ValueError:
            continue
        citations.append(SourceCitation(line=match.group("line").strip(), page=page))
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


def _set_selected_citation_page(page: int, pdf_name: str | None = None) -> None:
    selected_name = pdf_name or _select_default_pdf_name()
    if not selected_name:
        return
    bounded_page = max(1, page)
    st.session_state.selected_citation_pdf = selected_name
    st.session_state.selected_citation_page = bounded_page
    files_by_upload = st.session_state.get("pdf_viewer_files_by_upload") or {}
    pdf_bytes = files_by_upload.get(selected_name)
    if pdf_bytes is not None:
        page_count = count_pdf_pages(pdf_bytes)
        bounded = min(bounded_page, page_count)
        slider_key = _slider_key_for_pdf(selected_name, pdf_bytes)
        nav_key = _nav_input_key_for_pdf(selected_name, pdf_bytes)
        st.session_state[slider_key] = bounded
        st.session_state[nav_key] = bounded


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
            citation_buffer.append(
                (
                    line_index,
                    SourceCitation(
                        line=match.group("line").strip(),
                        page=int(match.group("page")),
                    ),
                )
            )
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
) -> None:
    """Render source lines with View page actions in a compact vertical stack."""
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
                    "View page",
                    key=f"view_citation_{message_key}_{line_index}_{citation_index}",
                    type="secondary",
                    width="stretch",
                    on_click=_set_selected_citation_page,
                    args=(citation.page, pdf_name),
                )


def render_response_with_citation_actions(text: str, message_key: str) -> None:
    """Render assistant markdown and add a View page action beside source lines."""
    citation_segment_index = 0
    for segment_type, content in _split_response_segments(text):
        if segment_type == "markdown":
            st.markdown(content)
            continue
        _render_citation_rows(content, message_key, citation_segment_index)
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

    info_col, nav_col = st.columns(
        [1.4, 1], gap="small", vertical_alignment="center"
    )
    with info_col:
        st.caption(f"Viewing **{filename}**")
    with nav_col:
        _render_page_nav(slider_key, nav_key, page_count)

    page_col, zoom_col = st.columns(2, gap="small")
    with page_col:
        selected_page = st.slider(
            "Page",
            min_value=1,
            max_value=page_count,
            key=slider_key,
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
    pdf_viewer(
        pdf_bytes,
        pages_to_render=[selected_page],
        width="100%",
        height=PDF_VIEWER_HEIGHT_PX,
        zoom_level=zoom_percent / 100.0,
        show_page_separator=False,
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
