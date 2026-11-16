"""Checklist form: constrained LLM fill, parsing, and MD/DOCX export."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal

import streamlit as st

from utils.checklist_schema import (
    DOCX_NOTES_TEMPLATE_LABEL,
    DOCX_LETTER_NUMBERED_MAJOR_SECTIONS,
    DOCX_LABEL_PREFIX_RE,
    ChecklistField,
    attach_docx_refs,
    build_docx_letter_export_prefixes,
    is_embedded_subfield_ref,
    load_prompts_csv,
)
from utils.config_vars import CHECKLIST_PROMPTS_CSV, CHECKLIST_TEMPLATE_DOCX
from utils.docx_utils import add_footnote_citations, add_markdown_paragraph, append_markdown_to_doc, get_docx_bytes
from utils.llm_prompts import (
    OPENAI_DEFAULT_MODEL,
    build_pdf_checklist_batch_system_prompt,
    fields_to_batch_payload,
)
from utils.openai_client import (
    generate_checklist_batch,
    get_openai_client,
    parse_checklist_batch_response,
    resolve_openai_api_key,
)
from utils.retrieval import format_excerpts, retrieve_for_checklist

PAGE_REF_RE = re.compile(r"\bpage\s+\d+\b", re.IGNORECASE)
NOT_SPECIFIED = "Not specified"
CANNOT_ANSWER_FROM_KB = "cannot_answer_from_kb"
KB_NOT_FOUND_EXPORT_LABEL = "Not found in specification"
SOURCE_URL_RE = re.compile(r"Source:\s*(https?://\S+)", re.IGNORECASE)
PROMPT_LABEL_PREFIX_RE = re.compile(r"^\d+\.\s*")
DOCX_EXPORT_LABEL_PREFIX_RE = re.compile(r"^(\d+|[A-Z]+)\.\s*")
DOCX_NOTES_EXPORT_PREFIX_RE = re.compile(
    r"^\d+\.\s*Additional Information/Notes:\s*",
    re.IGNORECASE,
)
SECTION_REF_RE = re.compile(
    r"\bSection\s+[\d][\d\s.\-/]*[\d]?(?:\s*,\s*[\d][\d\s.\-/]*[\d]?)*",
    re.IGNORECASE,
)
MAX_URL_CITATIONS = 3
MAX_SECTION_CITATIONS = 2

_MISSING_CITATION_NOTE = "Missing Section/page citation in explanation."

_SILENT_EXPLANATION_RE = re.compile(
    r"\b("
    r"not\s+specified|"
    r"does\s+not\s+specify|"
    r"do\s+not\s+specify|"
    r"pdf\s+silent|"
    r"uploaded\s+pdf\s+silent|"
    r"no\s+explicit|"
    r"not\s+in\s+the\s+uploaded|"
    r"not\s+stated\s+in"
    r")\b",
    re.IGNORECASE,
)


def _explanation_looks_silent(explanation: str) -> bool:
    """True when explanation states the PDF does not specify the value."""
    text = (explanation or "").strip()
    if not text:
        return False
    return bool(_SILENT_EXPLANATION_RE.search(text))


def _missing_citation_needs_review(
    *,
    field_status: str,
    citations: list[str],
    explanation: str,
    parsed_answer: str,
) -> bool:
    """Flag answered rows whose explanation lacks parseable Section/page cites."""
    if field_status != "answered" or citations:
        return False
    if not is_checklist_answered_value(parsed_answer):
        return False
    return not _explanation_looks_silent(explanation)


@dataclass
class ChecklistFootnote:
    """Footnote appendix entry for one answered or unsupported checklist field."""

    number: int
    row_index: int
    label: str
    answer: str
    explanation: str
    citations: list[str] = field(default_factory=list)
    status: str = ""


def _normalize_answer_text(text: str) -> str:
    if not text:
        return ""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip().strip('"').strip("'")
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    first_line = cleaned.split("\n")[0].strip()
    return first_line


def is_checklist_answered_value(value: Any) -> bool:
    """Return True only for values that should be exported as answered."""
    if value is None:
        return False
    if isinstance(value, list):
        return any(is_checklist_answered_value(item) for item in value)
    text = str(value).strip()
    return bool(text) and text.lower() != NOT_SPECIFIED.lower()


def parse_checklist_answer(raw: str, field: ChecklistField) -> tuple[str, bool, str]:
    """
    Map LLM output to a checklist value.

    Returns (parsed_answer, parse_valid, parse_note).
    """
    normalized = _normalize_answer_text(raw)
    if not normalized:
        return NOT_SPECIFIED, False, "empty response"

    if NOT_SPECIFIED.lower() in normalized.lower() and len(normalized) < 30:
        return NOT_SPECIFIED, True, ""

    if field.field_kind == "choice" and field.allowed_options:
        lower_norm = normalized.lower()
        for opt in field.allowed_options:
            if opt.lower() == lower_norm:
                return opt, True, ""
            if opt.lower() in lower_norm:
                return opt, True, "matched substring"
        for opt in field.allowed_options:
            if lower_norm in opt.lower():
                return opt, True, "matched substring"
        return normalized, False, f"not in allowed options: {field.allowed_options}"

    if field.field_kind in {"text", "notes"}:
        if len(normalized) > 500:
            return normalized[:500], True, "truncated"
        return normalized, True, ""

    return normalized, True, ""


def _checklist_retrieval_queries(fields: list[ChecklistField]) -> list[tuple[int, str, str]]:
    """(row_index, semantic query, keyword query) per field; keywords favor the field label."""
    return [
        (
            field.row_index,
            (field.prompt or "").strip(),
            f"{field.display_label} {field.category}",
        )
        for field in fields
    ]


def run_checklist_batch_autofill(
    fields: list[ChecklistField],
    documents: list[dict[str, Any]],
    *,
    model: str = OPENAI_DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Retrieve excerpts per field, then run one OpenAI batch call for up to 10 fields."""
    if not fields:
        return []
    client = get_openai_client(resolve_openai_api_key())
    excerpts, excerpt_ids_by_row = retrieve_for_checklist(
        client, documents, _checklist_retrieval_queries(fields)
    )
    payload = fields_to_batch_payload(fields, excerpt_ids_by_row)
    raw = generate_checklist_batch(
        client,
        model,
        payload,
        format_excerpts(excerpts),
        system_instruction=build_pdf_checklist_batch_system_prompt(),
    )
    rows = parse_checklist_batch_response(raw)
    by_index = {int(r.get("row_index")): r for r in rows if r.get("row_index") is not None}
    results: list[dict[str, Any]] = []
    for field in fields:
        row = by_index.get(field.row_index, {})
        answer = str(row.get("answer", "") or "").strip()
        explanation = str(row.get("explanation", "") or "").strip()
        raw_single = json.dumps({"answer": answer, "explanation": explanation})
        from utils.checklist_answer_explainer import parse_answer_explanation

        parsed, explanation, parse_valid, parse_note = parse_answer_explanation(
            raw_single, field
        )
        citations = extract_citations_from_answer(
            f"{answer} {explanation}"
        )
        field_status = "answered"
        status_reason = ""
        if not parse_valid:
            field_status = "needs_review"
        elif not is_checklist_answered_value(parsed):
            field_status = CANNOT_ANSWER_FROM_KB
            status_reason = (
                "No supported answer was found in the uploaded specification PDF."
            )
        elif _missing_citation_needs_review(
            field_status=field_status,
            citations=citations,
            explanation=explanation,
            parsed_answer=parsed,
        ):
            field_status = "needs_review"
            parse_note = _MISSING_CITATION_NOTE
        results.append(
            {
                "row_index": field.row_index,
                "category": field.category,
                "prompt": field.prompt,
                "possible_answers": field.possible_answers_raw,
                "field_kind": field.field_kind,
                "model": model,
                "response": raw_single,
                "parsed_answer": parsed,
                "llm_explanation": explanation,
                "parse_valid": parse_valid,
                "parse_note": parse_note,
                "field_status": field_status,
                "status_reason": status_reason,
                "citations": citations,
                "step": "done",
            }
        )
    return results


def extract_citations_from_answer(
    text: str,
    *,
    max_sections: int = MAX_SECTION_CITATIONS,
    max_pages: int = 3,
) -> list[str]:
    """Extract section and page references from model answer text."""
    citations: list[str] = []
    seen: set[str] = set()
    for match in SECTION_REF_RE.finditer(text or ""):
        ref = re.sub(r"\s+", " ", match.group(0).strip())
        if ref not in seen:
            seen.add(ref)
            citations.append(ref)
        if len(citations) >= max_sections:
            break
    for match in PAGE_REF_RE.finditer(text or ""):
        ref = match.group(0).strip()
        if ref not in seen:
            seen.add(ref)
            citations.append(ref)
        if len([c for c in citations if c.lower().startswith("page")]) >= max_pages:
            break
    return citations


def extract_citations_from_context(
    context: str,
    *,
    max_urls: int = MAX_URL_CITATIONS,
    max_sections: int = MAX_SECTION_CITATIONS,
) -> list[str]:
    """Minimal citation list: PDF URLs and spec section refs from retrieval text."""
    citations: list[str] = []
    seen: set[str] = set()
    for match in SOURCE_URL_RE.finditer(context or ""):
        url = match.group(1).rstrip(".,);]")
        if url not in seen:
            seen.add(url)
            citations.append(url)
        if len([c for c in citations if c.startswith("http")]) >= max_urls:
            break
    for match in SECTION_REF_RE.finditer(context or ""):
        ref = re.sub(r"\s+", " ", match.group(0).strip())
        if ref not in seen:
            seen.add(ref)
            citations.append(ref)
        if len([c for c in citations if not c.startswith("http")]) >= max_sections:
            break
    return citations


def format_citation_bullets_markdown(citations: list[str]) -> str:
    """Render citations as one markdown bullet list (avoids extra Streamlit block gaps)."""
    lines: list[str] = []
    for cite in citations:
        text = str(cite).strip()
        if not text:
            continue
        if text.startswith("http"):
            lines.append(f"- [{text}]({text})")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines)


def format_citations_markdown(citations: list[str]) -> str:
    """Citations heading plus a single compact bullet list."""
    bullets = format_citation_bullets_markdown(citations)
    if not bullets:
        return ""
    return f"**Citations:**\n{bullets}"


def _best_context_excerpt(context: str, query: str) -> str:
    """Return the best-matching context chunk for the query (full text, not truncated)."""
    if not context or not context.strip():
        return ""
    query_terms = [
        t.lower()
        for t in re.findall(r"[A-Za-z0-9]{3,}", query)
        if t.lower() not in {"regarding", "what", "the", "for", "specification"}
    ][:6]
    chunks = [c.strip() for c in re.split(r"\n{2,}", context) if c.strip()]
    if not chunks:
        chunks = [context.strip()]
    best = chunks[0]
    best_score = -1
    for chunk in chunks:
        lower = chunk.lower()
        score = sum(1 for t in query_terms if t in lower)
        if score > best_score:
            best_score = score
            best = chunk
    return re.sub(r"\s+", " ", best)


def build_footnote_explanation(
    field: ChecklistField,
    parsed_answer: str,
    context: str,
    search_explanation: str,
    *,
    status: str = "",
    status_reason: str = "",
) -> str:
    """Compose the explanation paragraph for the footnotes appendix."""
    parts: list[str] = []
    if status == CANNOT_ANSWER_FROM_KB:
        reason = (
            status_reason.strip()
            if status_reason and status_reason.strip()
            else "The uploaded specification PDF did not contain a supported answer."
        )
        parts.append(
            f"Prompt **{field.prompt_number}** — **{field.display_label}** was processed, "
            f"but it cannot be answered from the uploaded specification PDF. {reason}"
        )
    elif parsed_answer and parsed_answer.strip() and parsed_answer != NOT_SPECIFIED:
        parts.append(
            f"The specification excerpts support recording **{parsed_answer}** for "
            f"prompt **{field.prompt_number}** — **{field.display_label}**."
        )
    else:
        parts.append(
            f"Retrieved excerpts do not state an explicit requirement for "
            f"prompt **{field.prompt_number}** — **{field.display_label}**; "
            f"the form records **{NOT_SPECIFIED}**."
        )
    if search_explanation and search_explanation.strip():
        parts.append(search_explanation.strip())
    excerpt = _best_context_excerpt(context, field.prompt)
    if excerpt:
        parts.append(f'Supporting excerpt: "{excerpt}"')
    return " ".join(parts)


def compose_llm_footnote(
    field: ChecklistField,
    parsed_answer: str,
    llm_explanation: str,
    context: str,
    *,
    status: str = "",
    status_reason: str = "",
    search_explanation: str = "",
) -> str:
    """Footnote body from LLM explanation, with lead-in and supporting excerpt."""
    expl = (llm_explanation or "").strip()
    if not expl:
        return build_footnote_explanation(
            field,
            parsed_answer,
            context,
            search_explanation,
            status=status,
            status_reason=status_reason,
        )
    return f"Prompt **{field.prompt_number}** — **{field.display_label}**. {expl}"


def build_field_footnote(
    field: ChecklistField,
    parsed_answer: str,
    context: str,
    search_explanation: str,
    *,
    footnote_number: int,
    status: str = "",
    status_reason: str = "",
) -> ChecklistFootnote:
    """Build one footnote record for an answered or unsupported field."""
    citations = extract_citations_from_context(context)
    if not citations and context:
        citations = ["No PDF source URLs in retrieved context for this field."]
    elif not citations:
        citations = ["No retrieval context available for this field."]
    return ChecklistFootnote(
        number=footnote_number,
        row_index=field.row_index,
        label=field.display_label,
        answer=str(parsed_answer) if parsed_answer is not None else "",
        explanation=build_footnote_explanation(
            field,
            parsed_answer,
            context,
            search_explanation,
            status=status,
            status_reason=status_reason,
        ),
        citations=citations,
        status=status,
    )


def build_footnotes_for_export(
    fields: list[ChecklistField],
    values: dict[int, Any],
    footnote_meta: dict[int, dict[str, Any]] | None = None,
) -> list[ChecklistFootnote]:
    """
    Build ordered footnotes for answered fields and cannot-answer statuses.

    footnote_meta maps row_index to retrieval metadata plus optional status fields.
    """
    meta = footnote_meta or {}
    footnotes: list[ChecklistFootnote] = []
    for f in fields:
        val = values.get(f.row_index)
        row_meta = meta.get(f.row_index, {})
        status = row_meta.get("status", "")
        if is_checklist_answered_value(val):
            if isinstance(val, list):
                answer = ", ".join(str(v) for v in val if v)
            else:
                answer = str(val).strip()
        elif status == CANNOT_ANSWER_FROM_KB:
            answer = KB_NOT_FOUND_EXPORT_LABEL
        else:
            continue
        if not answer:
            continue
        context = row_meta.get("retrieved_context", "")
        search_explanation = row_meta.get("explanation", "")
        stored_footnote = (row_meta.get("footnote_explanation") or "").strip()
        if stored_footnote:
            citations = row_meta.get("citations") or extract_citations_from_context(context)
            if not citations and context:
                citations = ["No PDF source URLs in retrieved context for this field."]
            elif not citations:
                citations = ["No retrieval context available for this field."]
            footnotes.append(
                ChecklistFootnote(
                    number=f.prompt_number,
                    row_index=f.row_index,
                    label=f.display_label,
                    answer=answer,
                    explanation=stored_footnote,
                    citations=citations,
                    status=status,
                )
            )
        else:
            footnotes.append(
                build_field_footnote(
                    f,
                    answer,
                    context,
                    search_explanation,
                    footnote_number=f.prompt_number,
                    status=status,
                    status_reason=row_meta.get("status_reason", ""),
                )
            )
    return footnotes


def footnote_to_markdown(fn: ChecklistFootnote) -> list[str]:
    """Render one footnote as markdown lines."""
    lines = [
        f"### {fn.number}. {fn.label}",
        "",
        f"**{'Status' if fn.status == CANNOT_ANSWER_FROM_KB else 'Answer'} recorded:** {fn.answer}",
        "",
        f"**Explanation:** {fn.explanation}",
        "",
        "**Citations:**",
    ]
    for cite in fn.citations:
        if cite.startswith("http"):
            lines.append(f"- [{cite}]({cite})")
        else:
            lines.append(f"- {cite}")
    lines.append("")
    return lines


def append_footnotes_markdown(body: str, footnotes: list[ChecklistFootnote]) -> str:
    """Append Footnotes and citations section to markdown export."""
    if not footnotes:
        return body
    lines = [body.rstrip(), "", "## Footnotes and citations", ""]
    for fn in footnotes:
        lines.extend(footnote_to_markdown(fn))
    return "\n".join(lines).strip() + "\n"


def _export_answer_text(value: Any) -> str:
    """Return display text only for values that should be treated as answered."""
    if not is_checklist_answered_value(value):
        return ""
    if isinstance(value, list):
        return ", ".join(
            str(item).strip()
            for item in value
            if is_checklist_answered_value(item)
        )
    return str(value).strip()


@st.cache_data
def get_active_checklist_fields() -> list[ChecklistField]:
    """Load CSV fields with DOCX refs attached (cached for Streamlit reruns)."""
    fields = load_prompts_csv(CHECKLIST_PROMPTS_CSV)
    return attach_docx_refs(fields, CHECKLIST_TEMPLATE_DOCX)


ChecklistExportFilter = Literal["all", "answered", "kb_not_found"]


def _field_in_answered_export(field: ChecklistField, values: dict[int, Any]) -> bool:
    return is_checklist_answered_value(values.get(field.row_index))


def _field_in_kb_not_found_export(
    field: ChecklistField,
    footnote_meta: dict[int, dict[str, Any]] | None,
) -> bool:
    meta = (footnote_meta or {}).get(field.row_index, {})
    return meta.get("status") == CANNOT_ANSWER_FROM_KB


def _fields_for_markdown_export(
    fields: list[ChecklistField],
    values: dict[int, Any],
    footnote_meta: dict[int, dict[str, Any]] | None,
    export_filter: ChecklistExportFilter,
) -> list[ChecklistField]:
    if export_filter == "all":
        return fields
    if export_filter == "answered":
        return [f for f in fields if _field_in_answered_export(f, values)]
    return [f for f in fields if _field_in_kb_not_found_export(f, footnote_meta)]


def export_checklist_markdown(
    fields: list[ChecklistField],
    values: dict[int, Any],
    footnote_meta: dict[int, dict[str, Any]] | None = None,
    *,
    export_filter: ChecklistExportFilter = "all",
) -> str:
    """Build markdown export grouped by category, with footnotes appendix."""
    export_fields = _fields_for_markdown_export(
        fields, values, footnote_meta, export_filter
    )
    lines: list[str] = ["# Spec. Compliance Review Checklist", ""]
    current_cat: str | None = None
    for f in export_fields:
        if f.category != current_cat:
            current_cat = f.category
            lines.append(f"## {current_cat}")
            lines.append("")
        val = values.get(f.row_index)
        display = _export_answer_text(val)
        if display:
            lines.append(
                f"- [x] **{f.prompt_number}.** **{f.display_label}:** {display}"
            )
        elif export_filter == "kb_not_found":
            lines.append(
                f"- [ ] **{f.prompt_number}.** **{f.display_label}:** "
                f"_{KB_NOT_FOUND_EXPORT_LABEL}_"
            )
        else:
            lines.append(
                f"- [ ] **{f.prompt_number}.** **{f.display_label}:** _Unanswered_"
            )
    lines.append("")
    body = "\n".join(lines)
    footnotes = build_footnotes_for_export(export_fields, values, footnote_meta)
    return append_footnotes_markdown(body, footnotes)


def _set_cell_text(cell, text: str) -> None:
    cell.text = text


def _paragraph_text(element) -> str:
    """Visible text in a Word paragraph (w:t runs)."""

    return "".join(t.text or "" for t in element.iter() if t.tag.endswith("}t"))


def _normalize_option_label(text: str) -> str:
    normalized = " ".join((text or "").split()).strip().lower()
    for ch in ('\u201c', '\u201d', '\u2033', '\u2032'):
        normalized = normalized.replace(ch, '"')
    normalized = normalized.replace("\ufffd", '"')
    for fraction, ascii_text in {
        "\u00bc": "1/4",
        "\u00bd": "1/2",
        "\u00be": "3/4",
    }.items():
        normalized = normalized.replace(fraction, ascii_text)
    normalized = normalized.replace(' "1/4"', ' 1/4"')
    normalized = normalized.strip('"')
    return normalized


def _paragraph_matches_option(paragraph, option: str) -> bool:
    """True when paragraph label text matches a checklist option."""
    para = _normalize_option_label(_paragraph_text(paragraph))
    opt = _normalize_option_label(option)
    if not para or not opt:
        return False
    return para == opt or para.endswith(opt)


def _set_legacy_checkboxes_in_element(element, checked: bool) -> bool:
    """Set all legacy Word checkbox controls under an XML element."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    found = False
    val = "1" if checked else "0"
    for checkbox in element.iter(qn("w:checkBox")):
        found = True
        for tag in ("w:default", "w:checked"):
            child = checkbox.find(qn(tag))
            if child is None:
                child = OxmlElement(tag)
                checkbox.append(child)
            child.set(qn("w:val"), val)
    return found


def _set_legacy_checkboxes_in_paragraph(paragraph, checked: bool) -> bool:
    """Set checkbox control(s) in one paragraph only (multi-option cells)."""
    from docx.oxml.ns import qn

    checkbox = paragraph.find(qn("w:checkBox"))
    if checkbox is None:
        checkbox = paragraph.find(f".//{qn('w:checkBox')}")
    if checkbox is None:
        return False
    return _set_legacy_checkboxes_in_element(paragraph, checked)


def _set_legacy_checkboxes_in_cell(cell, checked: bool) -> bool:
    """Set all legacy Word checkbox controls in a cell; return True if any exist."""
    return _set_legacy_checkboxes_in_element(cell._tc, checked)


def _set_option_checkbox_in_cell(cell, option: str, checked: bool) -> bool:
    """Set the checkbox beside a specific option label within a cell."""
    from docx.oxml.ns import qn

    found = False
    for paragraph in cell._tc.iter(qn("w:p")):
        if paragraph.find(f".//{qn('w:checkBox')}") is None:
            continue
        if not _paragraph_matches_option(paragraph, option):
            continue
        if _set_legacy_checkboxes_in_paragraph(paragraph, checked):
            found = True
    return found


def _clear_row_choice_checkboxes(row) -> None:
    """Clear every checkbox in a choice row (all option columns)."""
    for ci in range(1, len(row.cells)):
        _set_legacy_checkboxes_in_cell(row.cells[ci], False)


def _clear_option_cells(table, docx_ref) -> None:
    """Clear all checkbox controls in a choice row."""
    if not docx_ref:
        return
    _clear_row_choice_checkboxes(table.rows[docx_ref.row_index])


def _clear_text_cell(table, docx_ref) -> None:
    """Clear text/value cells mapped to a free-text row."""
    if not docx_ref:
        return
    row = table.rows[docx_ref.row_index]
    col = docx_ref.value_col_index if docx_ref.value_col_index is not None else 1
    if col < len(row.cells):
        _set_cell_text(row.cells[col], "")


def _docx_export_label_prefix(
    field: ChecklistField,
    letter_prefixes: dict[int, str],
) -> str:
    """Word export prompt prefix from CSV prompt_number."""
    return f"{field.prompt_number}. "


def _notes_export_cell_text(
    field: ChecklistField,
    answer: str = "",
    *,
    letter_prefixes: dict[int, str] | None = None,
) -> str:
    """Full merged-cell text for a numbered Additional Information/Notes row."""
    prefixes = letter_prefixes or {}
    base = f"{_docx_export_label_prefix(field, prefixes)}{DOCX_NOTES_TEMPLATE_LABEL}"
    text = (answer or "").strip()
    return f"{base} {text}".strip() if text else base


def _clear_notes_cell(table, docx_ref) -> None:
    """Reset a notes row to the bare template label before export fill."""
    if not docx_ref:
        return
    row = table.rows[docx_ref.row_index]
    if row.cells:
        _set_cell_text(row.cells[0], DOCX_NOTES_TEMPLATE_LABEL)


def _write_notes_in_row(
    table,
    docx_ref,
    field: ChecklistField,
    text: str,
    *,
    letter_prefixes: dict[int, str] | None = None,
) -> None:
    """Write answer into merged Additional Information/Notes cell with export label."""
    if not docx_ref or not text:
        return
    row = table.rows[docx_ref.row_index]
    if row.cells:
        _set_cell_text(
            row.cells[0],
            _notes_export_cell_text(field, text, letter_prefixes=letter_prefixes),
        )


def _resolve_option_column(docx_ref, selected: str) -> int | None:
    if not docx_ref or not selected:
        return None
    col = docx_ref.option_col_map.get(selected)
    if col is not None:
        return col
    sel_lower = selected.lower()
    sel_norm = _normalize_option_label(selected)
    for opt, ci in docx_ref.option_col_map.items():
        if opt.lower() == sel_lower:
            return ci
        if _normalize_option_label(opt) == sel_norm:
            return ci
    return None


def _mark_option_in_row(table, docx_ref, selected: str) -> None:
    if not docx_ref or not selected:
        return
    row = table.rows[docx_ref.row_index]
    _clear_row_choice_checkboxes(row)
    marked = False
    for ci in range(1, len(row.cells)):
        if _set_option_checkbox_in_cell(row.cells[ci], selected, True):
            marked = True
    if marked:
        return
    col = _resolve_option_column(docx_ref, selected)
    if col is not None and col < len(row.cells):
        if not _set_legacy_checkboxes_in_cell(row.cells[col], True):
            _set_cell_text(row.cells[col], "X")


def _embedded_subfield_cell_text(
    field: ChecklistField,
    answer: str = "",
    *,
    letter_prefixes: dict[int, str] | None = None,
) -> str:
    """Label cell text for an embedded sub-column (e.g. H. Duration: 2 hours)."""
    prefixes = letter_prefixes or {}
    label = (
        f"{_docx_export_label_prefix(field, prefixes)}{field.display_label}".rstrip(":")
    )
    answer_text = (answer or "").strip()
    return f"{label}: {answer_text}".strip() if answer_text else f"{label}:"


def _append_embedded_prompt_to_label_cell(cell, field: ChecklistField) -> None:
    """Add embedded prompts to the main numbered prompt list cell."""
    text = cell.text.strip()
    prompt_label = f"{field.prompt_number}. {field.display_label.rstrip(':')}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(line.startswith(f"{field.prompt_number}. ") for line in lines):
        return
    lines.append(prompt_label)
    _set_cell_text(cell, "\n".join(lines))


def _write_text_in_row(
    table,
    docx_ref,
    text: str,
    field: ChecklistField | None = None,
    *,
    letter_prefixes: dict[int, str] | None = None,
) -> None:
    if not docx_ref or not text:
        return
    row = table.rows[docx_ref.row_index]
    if docx_ref.value_col_index is not None and docx_ref.value_col_index < len(row.cells):
        cell = row.cells[docx_ref.value_col_index]
        if field is not None and is_embedded_subfield_ref(docx_ref):
            _set_cell_text(cell, text)
        else:
            _set_cell_text(cell, text)
    elif len(row.cells) > 1:
        _set_cell_text(row.cells[1], text)


def _append_footnotes_docx(doc, footnotes: list[ChecklistFootnote]) -> None:
    """Add Footnotes and citations section at end of Word document."""
    if not footnotes:
        return
    doc.add_heading("Footnotes and citations", level=1)
    for fn in footnotes:
        doc.add_heading(f"{fn.number}. {fn.label}", level=2)
        label = "Status" if fn.status == CANNOT_ANSWER_FROM_KB else "Answer"
        add_markdown_paragraph(doc, f"**{label} recorded:** {fn.answer}")
        append_markdown_to_doc(doc, f"**Explanation:** {fn.explanation}")
        add_footnote_citations(doc, fn.citations)


def _apply_export_label_to_cell(
    cell,
    field: ChecklistField,
    *,
    letter_prefixes: dict[int, str],
) -> None:
    """Prefix exported template row label (column 0) with Word list label."""
    text = cell.text.strip()
    if field.field_kind == "notes":
        unprefixed = DOCX_EXPORT_LABEL_PREFIX_RE.sub("", text, count=1).strip()
        answer = ""
        if unprefixed.lower().startswith(DOCX_NOTES_TEMPLATE_LABEL.lower()):
            answer = unprefixed[len(DOCX_NOTES_TEMPLATE_LABEL) :].strip()
        _set_cell_text(
            cell,
            _notes_export_cell_text(
                field, answer, letter_prefixes=letter_prefixes
            ),
        )
        return
    prefix = _docx_export_label_prefix(field, letter_prefixes)
    text = DOCX_EXPORT_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    _set_cell_text(cell, prefix + text if text else prefix.strip())


def _apply_export_label_to_embedded_header(
    cell,
    field: ChecklistField,
    *,
    letter_prefixes: dict[int, str],
) -> None:
    """Prefix embedded sub-column header (e.g. Duration:) with export list label."""
    text = cell.text.strip()
    text = DOCX_EXPORT_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    answer = ""
    label = field.display_label.rstrip(":").strip()
    if text.lower().startswith(label.lower()) and ":" in text:
        answer = text.split(":", 1)[1].strip()
    _set_cell_text(
        cell,
        _embedded_subfield_cell_text(
            field, answer, letter_prefixes=letter_prefixes
        ),
    )


def _apply_prompt_numbers_to_docx_labels(
    doc,
    fields_with_refs: list[ChecklistField],
    letter_prefixes: dict[int, str] | None = None,
) -> None:
    """Stamp export labels on mapped row labels and embedded sub-column headers."""
    prefixes = letter_prefixes or {}
    for f in fields_with_refs:
        if f.docx_ref is None or f.docx_ref.table_index >= len(doc.tables):
            continue
        table = doc.tables[f.docx_ref.table_index]
        if f.docx_ref.row_index >= len(table.rows):
            continue
        row = table.rows[f.docx_ref.row_index]
        if is_embedded_subfield_ref(f.docx_ref):
            col = f.docx_ref.value_col_index
            if row.cells:
                _append_embedded_prompt_to_label_cell(row.cells[0], f)
            if col is not None and col < len(row.cells):
                text = row.cells[col].text.strip()
                if text.startswith(f"{f.prompt_number}. "):
                    text = DOCX_EXPORT_LABEL_PREFIX_RE.sub("", text, count=1).strip()
                    label = f.display_label.rstrip(":").strip()
                    if text.lower().startswith(label.lower()) and ":" in text:
                        text = text.split(":", 1)[1].strip()
                    _set_cell_text(row.cells[col], text)
            continue
        if row.cells:
            _apply_export_label_to_cell(
                row.cells[0], f, letter_prefixes=prefixes
            )


def _apply_section_letters_to_docx_headers(doc) -> None:
    """Stamp letter prefixes on configured section header rows."""
    section_letters = {
        name: f"{chr(65 + index)}. "
        for index, name in enumerate(DOCX_LETTER_NUMBERED_MAJOR_SECTIONS)
    }
    for table in doc.tables:
        for row in table.rows:
            if not row.cells:
                continue
            cell = row.cells[0]
            label = DOCX_LABEL_PREFIX_RE.sub("", cell.text.strip(), count=1).strip()
            prefix = section_letters.get(label)
            if prefix:
                _set_cell_text(cell, f"{prefix}{label}")


def _project_header_fields(
    fields_with_refs: list[ChecklistField],
) -> tuple[ChecklistField | None, ChecklistField | None]:
    number = next((f for f in fields_with_refs if f.display_label == "Project Number"), None)
    name = next((f for f in fields_with_refs if f.display_label == "Project Name"), None)
    return number, name


def _write_project_header_table(
    table,
    fields_with_refs: list[ChecklistField],
    values: dict[int, Any],
    letter_prefixes: dict[int, str] | None = None,
) -> None:
    """Fill table 0 project number/name from CSV fields (not fixed row_index 0/1)."""
    prefixes = letter_prefixes or {}
    pn_field, pname_field = _project_header_fields(fields_with_refs)
    if pn_field and len(table.rows) >= 1 and len(table.rows[0].cells) > 1:
        pn = _export_answer_text(values.get(pn_field.row_index))
        if pn:
            _set_cell_text(table.rows[0].cells[1], pn)
        if table.rows[0].cells:
            _apply_export_label_to_cell(
                table.rows[0].cells[0], pn_field, letter_prefixes=prefixes
            )
    if pname_field and len(table.rows) >= 2 and len(table.rows[1].cells) > 1:
        pname = _export_answer_text(values.get(pname_field.row_index))
        if pname:
            _set_cell_text(table.rows[1].cells[1], pname)
        if table.rows[1].cells:
            _apply_export_label_to_cell(
                table.rows[1].cells[0], pname_field, letter_prefixes=prefixes
            )


def _clear_export_template_fields(doc, fields_with_refs: list[ChecklistField]) -> None:
    """Remove template/example values so unanswered export fields stay blank."""
    _set_legacy_checkboxes_in_element(doc.element, False)

    if len(doc.tables) > 0:
        t0 = doc.tables[0]
        if len(t0.rows) >= 2:
            if len(t0.rows[0].cells) > 1:
                _set_cell_text(t0.rows[0].cells[1], "")
            if len(t0.rows[1].cells) > 1:
                _set_cell_text(t0.rows[1].cells[1], "")

    for f in fields_with_refs:
        if f.docx_ref is None or f.docx_ref.table_index >= len(doc.tables):
            continue
        table = doc.tables[f.docx_ref.table_index]
        if f.field_kind == "choice":
            _clear_option_cells(table, f.docx_ref)
        elif f.field_kind == "notes":
            _clear_notes_cell(table, f.docx_ref)
        else:
            _clear_text_cell(table, f.docx_ref)


def export_checklist_docx(
    fields: list[ChecklistField],
    values: dict[int, Any],
    template_path: str | Path | None = None,
    footnote_meta: dict[int, dict[str, Any]] | None = None,
) -> bytes | None:
    """Fill checklist template DOCX with values; returns bytes or None on failure."""
    from docx import Document

    root = Path(__file__).resolve().parent.parent
    path = Path(template_path) if template_path else root / CHECKLIST_TEMPLATE_DOCX
    if not path.is_file():
        return None

    doc = Document(str(path))
    fields_with_refs = attach_docx_refs(fields, path)
    letter_prefixes = build_docx_letter_export_prefixes(fields_with_refs, path)
    _clear_export_template_fields(doc, fields_with_refs)
    _apply_prompt_numbers_to_docx_labels(doc, fields_with_refs, letter_prefixes)
    _apply_section_letters_to_docx_headers(doc)

    if len(doc.tables) > 0:
        _write_project_header_table(
            doc.tables[0], fields_with_refs, values, letter_prefixes
        )

    for f in fields_with_refs:
        if f.display_label in {"Project Number", "Project Name"}:
            continue
        text = _export_answer_text(values.get(f.row_index))
        if not text or f.docx_ref is None:
            continue
        if f.docx_ref.table_index >= len(doc.tables):
            continue
        table = doc.tables[f.docx_ref.table_index]
        if f.field_kind == "choice":
            _mark_option_in_row(table, f.docx_ref, text)
        elif f.field_kind == "notes":
            _write_notes_in_row(
                table, f.docx_ref, f, text, letter_prefixes=letter_prefixes
            )
        else:
            _write_text_in_row(
                table, f.docx_ref, text, field=f, letter_prefixes=letter_prefixes
            )

    _apply_prompt_numbers_to_docx_labels(doc, fields_with_refs, letter_prefixes)
    _apply_section_letters_to_docx_headers(doc)

    footnotes = build_footnotes_for_export(fields, values, footnote_meta)
    _append_footnotes_docx(doc, footnotes)
    return get_docx_bytes(doc)
