from utils.pdf_citation_viewer import (
    _split_response_segments,
    build_pdf_filename_citation_suffix,
    extract_source_citations,
    parse_pdf_name_from_source_line,
)


def test_build_pdf_filename_citation_suffix_empty():
    assert build_pdf_filename_citation_suffix([]) == ""
    assert build_pdf_filename_citation_suffix(["", "  "]) == ""


def test_build_pdf_filename_citation_suffix_single_pdf():
    name = "RSE1A IFC EP Design - Electrical SPEC.pdf"
    suffix = build_pdf_filename_citation_suffix([name])

    assert name in suffix
    assert "verbatim" in suffix.lower()
    assert f"Section 26 05 19, Page 15, {name}" in suffix
    assert "Multiple PDFs" not in suffix


def test_build_pdf_filename_citation_suffix_multiple_pdfs():
    electrical = "Electrical SPEC.pdf"
    mechanical = "Mechanical SPEC.pdf"
    suffix = build_pdf_filename_citation_suffix([electrical, mechanical])

    assert electrical in suffix
    assert mechanical in suffix
    assert "Multiple PDFs are attached" in suffix
    assert "different filenames" in suffix
    assert f"Section 26 05 19, Page 15, {electrical}" in suffix
    assert f"Section 23 05 00, Page 4, {mechanical}" in suffix


def test_extract_source_citations_classic_line():
    line = "Source 1: Section 26 05 19, Page 15"
    citations = extract_source_citations(line)

    assert len(citations) == 1
    assert citations[0].page == 15
    assert citations[0].line == line


def test_extract_source_citations_with_filename():
    line = "Source 1: Section 26 05 19, Page 15, Electrical SPEC.pdf"
    citations = extract_source_citations(line)

    assert len(citations) == 1
    assert citations[0].page == 15
    assert citations[0].line == line


def test_parse_pdf_name_from_source_line():
    electrical = "Electrical SPEC.pdf"
    mechanical = "Mechanical SPEC.pdf"
    line = f"Source 1: Section 26 05 19, Page 15, {electrical}"

    assert parse_pdf_name_from_source_line(line, [electrical, mechanical]) == electrical
    assert parse_pdf_name_from_source_line(line, [mechanical]) is None
    assert parse_pdf_name_from_source_line("Source 1: Section 26 05 19, Page 15", [electrical]) is None


def test_split_response_segments_emits_citations_for_both_formats():
    classic = "Source 1: Section 26 05 19, Page 15"
    with_filename = "Source 1: Section 26 05 19, Page 15, Electrical SPEC.pdf"

    classic_segments = _split_response_segments(f"**Sources**\n\n{classic}")
    filename_segments = _split_response_segments(f"**Sources**\n\n{with_filename}")

    assert any(seg_type == "citations" for seg_type, _ in classic_segments)
    assert any(seg_type == "citations" for seg_type, _ in filename_segments)


def test_split_response_segments_emits_citations_for_bulleted_sources():
    text = (
        "**Sources**\n\n"
        "- Source 1: Section 26 05 33, Page 8 of 12\n"
        "- Source 2: Section 26 05 33, Page 9 of 12\n"
    )
    segments = _split_response_segments(text)

    citation_segments = [content for seg_type, content in segments if seg_type == "citations"]
    assert len(citation_segments) == 1
    assert len(citation_segments[0]) == 2
    assert citation_segments[0][0][1].page == 8
    assert citation_segments[0][1][1].page == 9


def test_extract_source_citations_page_of_total():
    line = "Source 1: Section 26 05 33, Page 8 of 12"
    citations = extract_source_citations(f"- {line}")

    assert len(citations) == 1
    assert citations[0].page == 8
    assert citations[0].line == line
