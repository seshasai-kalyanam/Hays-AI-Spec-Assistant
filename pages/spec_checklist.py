"""Spec Compliance Review Checklist — fill form from specification prompts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import time
from pathlib import Path

import streamlit as st

from utils.login_landing import render_guest_email_gate
from utils.checklist_field_state import (
    diagnose_export_alignment,
    export_values,
    format_export_alignment_issue_line,
    format_export_alignment_summary,
    resolve_field_status,
)
from utils.checklist_schema import attach_docx_refs, format_prompt_label
from utils.config_vars import (
    CHECKLIST_DEFAULT_MODEL,
    CHECKLIST_MODELS,
    CHECKLIST_TEMPLATE_DOCX,
)
from utils.llm_errors import format_llm_error
from utils.llm_prompts import CHECKLIST_BATCH_SIZE
from utils.openai_client import resolve_openai_api_key
from utils.pdf_chat_ui import (
    init_pdf_session_state,
    loaded_spec_documents,
    pdf_upload_in_progress,
    pdf_uploads_ready,
    pending_uploader_files,
)
from utils.results_form import (
    CANNOT_ANSWER_FROM_KB,
    NOT_SPECIFIED,
    build_footnotes_for_export,
    compose_llm_footnote,
    export_checklist_docx,
    export_checklist_markdown,
    format_citations_markdown,
    get_active_checklist_fields,
    is_checklist_answered_value,
    run_checklist_batch_autofill,
)
from utils.ui_theme import inject_gpt_inspired_layout_css

FILTER_INFORMATION_NOT_FOUND_IN_KB = "Not found in specification"
NOT_IN_SPEC_DEFAULT_REASON = (
    "No supported answer was found in the uploaded specification PDF(s)."
)
FILTER_NOT_PROCESSED = "Not processed"
FILTER_PROCESSED_BY_AUTOFILL = "Processed by auto-fill"
FILTER_ANSWERED_BY_LLM = "Answered by LLM"
_LEGACY_STATUS_FILTER_LABELS = {
    "Cannot answer from KB": FILTER_INFORMATION_NOT_FOUND_IN_KB,
    "Information not found in KB": FILTER_INFORMATION_NOT_FOUND_IN_KB,
    "Unanswered": FILTER_NOT_PROCESSED,
    "Answered": FILTER_PROCESSED_BY_AUTOFILL,
    "Selected for LLM": "All fields",
    "Checked for auto-fill": "All fields",
}


def _init_session_state(fields) -> None:
    st.session_state.setdefault("checklist_values", {})
    st.session_state.setdefault("checklist_field_status", {})
    st.session_state.setdefault("checklist_footnote_meta", {})
    st.session_state.setdefault("checklist_user_overridden_rows", set())
    st.session_state.setdefault("checklist_selected_model", CHECKLIST_DEFAULT_MODEL)
    st.session_state.setdefault("cl_category_filter", "All categories")
    st.session_state.setdefault("cl_status_filter", "All fields")
    st.session_state.setdefault("cl_picker_search", "")
    st.session_state.setdefault("cl_picker_category", "All categories")
    for f in fields:
        key = f"llm_include_{f.row_index}"
        st.session_state.setdefault(key, False)


def _result_llm_text(result: dict) -> str:
    """Model explanation from batch autofill (llm_explanation) or legacy KB field."""
    return (result.get("llm_explanation") or result.get("explanation") or "").strip()


def _store_footnote_meta(row_index: int, result: dict) -> None:
    """Persist retrieval metadata used for footnotes appendix on export."""
    if not result:
        return
    status_entry = st.session_state.get("checklist_field_status", {}).get(row_index, {})
    st.session_state.checklist_footnote_meta[row_index] = {
        "retrieved_context": result.get("retrieved_context", ""),
        "explanation": _result_llm_text(result),
        "search_terms": result.get("search_terms", ""),
        "citations": result.get("citations", []),
        "footnote_explanation": result.get("footnote_explanation", ""),
        "status": status_entry.get("status", result.get("field_status", "")),
        "status_reason": status_entry.get("reason", result.get("status_reason", "")),
    }


def _user_overridden_rows() -> set:
    return st.session_state.setdefault("checklist_user_overridden_rows", set())


def _mark_row_user_overridden(row_index: int) -> None:
    _user_overridden_rows().add(row_index)


def _clear_row_user_override(row_index: int) -> None:
    _user_overridden_rows().discard(row_index)


def _footnote_meta_for_export() -> dict:
    """Merge stored autofill metadata with any log entries."""
    overridden = _user_overridden_rows()
    meta = dict(st.session_state.get("checklist_footnote_meta", {}))
    for row_index in overridden:
        meta.pop(row_index, None)
    for entry in st.session_state.get("checklist_autofill_log", []):
        row_index = entry.get("row_index")
        if row_index is None or row_index in overridden:
            continue
        entry_state = _autofill_result_state(entry)
        status = entry.get("field_status", "")
        status_reason = entry.get("status_reason", "")
        if entry_state == CANNOT_ANSWER_FROM_KB and not status:
            status = CANNOT_ANSWER_FROM_KB
            status_reason = status_reason or NOT_IN_SPEC_DEFAULT_REASON
        meta[row_index] = {
            "retrieved_context": entry.get("retrieved_context", ""),
            "explanation": _result_llm_text(entry),
            "search_terms": entry.get("search_terms", ""),
            "citations": entry.get("citations", []),
            "footnote_explanation": entry.get("footnote_explanation", ""),
            "status": status,
            "status_reason": status_reason,
        }
    for row_index, status_entry in st.session_state.get("checklist_field_status", {}).items():
        if not isinstance(status_entry, dict):
            continue
        meta.setdefault(row_index, {})
        meta[row_index]["status"] = status_entry.get("status", "")
        meta[row_index]["status_reason"] = status_entry.get("reason", "")
    return meta


def _checklist_model_fallback(usable: list[str]) -> str:
    if CHECKLIST_DEFAULT_MODEL in usable:
        return CHECKLIST_DEFAULT_MODEL
    return usable[0]


def _render_model_settings() -> None:
    usable = list(CHECKLIST_MODELS)
    if st.session_state.checklist_selected_model not in usable:
        st.session_state.checklist_selected_model = _checklist_model_fallback(usable)
    st.session_state.checklist_selected_model = st.selectbox(
        "Select Model",
        usable,
        index=usable.index(st.session_state.checklist_selected_model),
        help="Choose which LLM to use for generating responses",
    )


def _render_about_this_tool() -> None:
    spec_not_found = FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()
    st.markdown(
        "Fill the **Spec. Compliance Review Checklist** using "
        f"specification PDF(s) uploaded on the **Home** page (~{len(get_active_checklist_fields())} structured "
        "prompts from `db/prompts.csv`). Each row has allowed answers (checkbox options "
        "or short text), not open-ended chat prose."
    )
    st.markdown("**How answers are produced**")
    st.markdown(
        f"Auto-fill searches the indexed PDF(s) for each prompt and sends the matching excerpts to "
        f"**{CHECKLIST_DEFAULT_MODEL}** in batches of **{CHECKLIST_BATCH_SIZE}** prompts. The model "
        "must pick from allowed answers or return **Not specified**, using only the "
        "PDF—not general industry knowledge."
    )
    st.markdown("**Auto-fill workflow**")
    st.markdown(
        "1. **Choose prompts** — in **Choose fields to auto-fill**, search or filter by "
        "category, then check rows. Use **Check all visible** / **Check none visible** "
        "for the current filter, or **Check all** / **Check none** for the full form.\n"
        "2. **Run** — click **Auto-fill selected fields**. Only checked rows that "
        "have not been auto-filled yet are processed.\n"
        "3. **Review** — top metrics and **Last auto-fill run** summarize the batch. "
        "Expand a row there for specification citations, the model explanation, and the "
        "raw model JSON."
    )
    st.markdown("**Review checklist**")
    st.markdown(
        "Edit any cell after auto-fill. Each row shows possible answers and the current "
        "result. **Clear** resets the value, status, footnotes, and parse warnings."
    )
    st.markdown("**Review status filters**")
    st.markdown(
        "- **All fields** — full form (combine with **Category** to narrow).\n"
        "- **Last auto-fill** — only prompts from the most recent run (applied "
        "automatically right after auto-fill finishes).\n"
        f"- **{FILTER_PROCESSED_BY_AUTOFILL}** — answered, {spec_not_found}, or "
        "**Needs review** rows.\n"
        f"- **{FILTER_ANSWERED_BY_LLM}** — auto-fill accepted a valid answer into "
        "the checklist (excludes manual edits).\n"
        "- **Needs review** — parse warnings only.\n"
        f"- **{FILTER_INFORMATION_NOT_FOUND_IN_KB}** — blank cells with KB footnotes.\n"
        f"- **{FILTER_NOT_PROCESSED}** — never auto-filled and still blank."
    )
    st.markdown("**Row statuses**")
    st.markdown(
        "- **Needs review** — Auto-fill returned text the system cannot accept as a "
        "valid checklist answer (for example, a choice that is not in the allowed "
        "options). The cell may still show the model text, but you should correct it "
        "or **Clear** the row. These rows are omitted from Markdown and Word "
        "downloads until the warning is cleared.\n"
        f"- **{FILTER_INFORMATION_NOT_FOUND_IN_KB}** — No supported answer appeared "
        "in the uploaded specification PDF(s). The answer cell stays blank; details "
        "appear in footnotes and exports.\n"
        "- **Answered** — A valid value is recorded (from auto-fill or your edit).\n"
        "- **Error** — Auto-fill failed (OpenAI API or model error). **Clear** the row "
        "or edit the value manually."
    )
    st.markdown("**Footnotes and citations**")
    st.markdown(
        f"Answered and **{spec_not_found}** rows can include footnotes with "
        "specification citations. Preview them under "
        "**Footnotes and citations** at the bottom of the page. If you **manually** "
        "change an answer, prior auto-fill footnotes for that row are cleared."
    )
    st.markdown("**Export**")
    st.markdown(
        "When the form is ready, use **Export** at the bottom (left to right). Hover "
        "the **?** icon beside each button for details:\n"
        "- **Word (.docx)** — fills the official Hays checklist template "
        "(`spec_compliance_checklist.docx`; checkboxes and mapped text fields).\n"
        "- **Markdown (full)** — entire checklist with answered and unanswered rows "
        "(`spec_compliance_checklist.md`).\n"
        "- **Markdown (answered)** — only prompts with a filled answer "
        "(`spec_compliance_checklist_answered.md`).\n"
        "- **Markdown (not in specification)** — only prompts marked "
        f"**{FILTER_INFORMATION_NOT_FOUND_IN_KB}** "
        "(`spec_compliance_checklist_not_in_kb.md`).\n\n"
        "Exports reflect current form values and the latest auto-fill footnote "
        "metadata (except **Needs review** rows, which stay blank in downloads until "
        "corrected). Edit the form first if you need changes in the downloaded file."
    )
    st.markdown("**Session notes**")
    st.markdown(
        "Progress is kept in this browser session while you stay on the page. "
        "Refreshing or leaving may reset unchecked work. Sign-in is required; use "
        "**Home** to return to the main assistant."
    )

def _render_tools_sidebar() -> None:
    with st.sidebar:
        if st.button("Home", key="cl_nav_home", width="stretch"):
            st.switch_page("app.py")
        with st.expander("Settings"):
            _render_model_settings()
        with st.expander("Tools", expanded=False):
            if st.button("Spec Checklist", key="cl_nav_checklist", width="stretch"):
                st.switch_page("pages/spec_checklist.py")
        with st.expander("About this tool", expanded=False, icon=":material/help:"):
            _render_about_this_tool()


def _fields_by_category(fields):
    grouped: dict[str, list] = {}
    for f in fields:
        grouped.setdefault(f.category, []).append(f)
    return grouped


def _category_filter_options(fields):
    return ["All categories"] + sorted(_fields_by_category(fields).keys())


def _sort_fields_by_prompt_number(fields) -> list:
    return sorted(fields, key=lambda item: item.prompt_number)


def _field_value(field) -> str:
    return str(st.session_state.get("checklist_values", {}).get(field.row_index, "") or "").strip()


def _is_answered(field) -> bool:
    return is_checklist_answered_value(_field_value(field))


def _field_status_entry(row_index: int) -> dict:
    entry = st.session_state.get("checklist_field_status", {}).get(row_index, {})
    return entry if isinstance(entry, dict) else {}


def _latest_autofill_entry_for_row(row_index: int) -> dict:
    for entry in reversed(st.session_state.get("checklist_autofill_log", [])):
        if entry.get("row_index") == row_index:
            return entry
    return {}


def _field_status(field) -> str:
    if field.row_index in _user_overridden_rows():
        latest_log_state = None
    else:
        latest_entry = _latest_autofill_entry_for_row(field.row_index)
        latest_log_state = _autofill_result_state(latest_entry) if latest_entry else None
    return resolve_field_status(
        is_answered=_is_answered(field),
        has_review_warning=_has_review_warning(field),
        stored_status=_field_status_entry(field.row_index).get("status", ""),
        latest_log_state=latest_log_state,
    )


def _is_processed_by_llm(field) -> bool:
    return _field_status(field) in {"answered", CANNOT_ANSWER_FROM_KB, "needs_review"}


def _is_answered_by_llm(field) -> bool:
    """True when auto-fill parsed a valid answer into the checklist."""
    if _has_review_warning(field) or not _is_answered(field):
        return False
    if field.row_index in _user_overridden_rows():
        return False
    latest = _latest_autofill_entry_for_row(field.row_index)
    return bool(latest) and _autofill_result_state(latest) == "answered"


def _field_pending_autofill(field) -> bool:
    """True when the field is eligible for a new auto-fill run."""
    return not _is_processed_by_llm(field)


def _set_field_status(row_index: int, status: str, *, reason: str = "", result: dict | None = None) -> None:
    entry = {
        "status": status,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if result:
        entry["model"] = result.get("model", "")
        entry["search_terms"] = result.get("search_terms", "")
    st.session_state.checklist_field_status[row_index] = entry


def _has_review_warning(field) -> bool:
    return bool(st.session_state.get(f"checklist_parse_warn_{field.row_index}"))


def _row_needs_review(row_index: int) -> bool:
    return bool(st.session_state.get(f"checklist_parse_warn_{row_index}"))


def _review_row_indices(fields) -> frozenset[int]:
    return frozenset(f.row_index for f in fields if _row_needs_review(f.row_index))


def _values_for_export(values: dict, fields) -> dict:
    return export_values(values, _review_row_indices(fields))


def _export_withheld_count(fields) -> int:
    return sum(1 for f in fields if _row_needs_review(f.row_index))


def _render_export_alignment_warnings(fields, values: dict) -> None:
    """Show when answered UI values will not appear in Word export."""
    fields_with_refs = attach_docx_refs(fields, CHECKLIST_TEMPLATE_DOCX)
    report = diagnose_export_alignment(
        fields_with_refs,
        values,
        _review_row_indices(fields_with_refs),
    )
    summary = format_export_alignment_summary(report)
    if not summary:
        return
    st.warning(summary, icon=":material/warning:")
    with st.expander("Word export alignment details", expanded=False):
        for issue in report.issues[:25]:
            st.caption(format_export_alignment_issue_line(issue))
        if len(report.issues) > 25:
            st.caption(f"… and **{len(report.issues) - 25}** more.")


def _clear_footnote_meta_for_row(row_index: int) -> None:
    st.session_state.checklist_footnote_meta.pop(row_index, None)
    _mark_row_user_overridden(row_index)


def _autofill_result_state(result: dict) -> str:
    """User-facing state for one auto-fill result."""
    if result.get("error"):
        return "error"
    if result.get("step") != "done":
        return "running"
    if result.get("field_status") == CANNOT_ANSWER_FROM_KB:
        return CANNOT_ANSWER_FROM_KB
    if result.get("parse_valid") is False:
        return "review"
    if is_checklist_answered_value(result.get("parsed_answer")):
        return "answered"
    return CANNOT_ANSWER_FROM_KB


def _summarize_autofill_log(log: list[dict]) -> dict[str, int]:
    summary = {key: 0 for key in ("answered", CANNOT_ANSWER_FROM_KB, "review", "error", "running")}
    for entry in log:
        state = _autofill_result_state(entry)
        summary[state] = summary.get(state, 0) + 1
    return summary


def _last_autofill_row_indices() -> set[int]:
    return {
        entry.get("row_index")
        for entry in st.session_state.get("checklist_autofill_log", [])
        if entry.get("row_index") is not None
    }


def _selected_fields(fields) -> list:
    return [f for f in fields if st.session_state.get(f"llm_include_{f.row_index}", False)]


def _fields_to_autofill(selected: list) -> list:
    """Checked fields that have not yet been processed by auto-fill."""
    return [f for f in selected if _field_pending_autofill(f)]


def _autofill_selection_counts(fields) -> dict[str, int]:
    """Checked fields in the picker and how many will run on the next auto-fill."""
    selected = _selected_fields(fields)
    pending = [f for f in selected if _field_pending_autofill(f)]
    return {
        "selected": len(selected),
        "pending": len(pending),
    }


def _sync_field_widget_from_value(field, value: str) -> None:
    """Keep form widgets aligned when auto-fill updates stored values."""
    value = str(value or "")
    if field.field_kind == "choice" and field.allowed_options:
        for opt in field.allowed_options:
            st.session_state[f"cl_ch_{field.row_index}_{opt}"] = value == opt
        return

    if field.field_kind == "notes":
        st.session_state[f"cl_ta_{field.row_index}"] = value
    else:
        st.session_state[f"cl_ti_{field.row_index}"] = value


def _clear_field_value(field) -> None:
    st.session_state.checklist_values[field.row_index] = ""
    st.session_state.checklist_field_status.pop(field.row_index, None)
    _clear_footnote_meta_for_row(field.row_index)
    st.session_state.pop(f"checklist_parse_warn_{field.row_index}", None)
    _sync_field_widget_from_value(field, "")


def _set_all_llm_include(fields, value: bool) -> None:
    for f in fields:
        st.session_state[f"llm_include_{f.row_index}"] = value


def _apply_check_all_visible(fields) -> None:
    for f in _picker_visible_fields(fields):
        st.session_state[f"llm_include_{f.row_index}"] = True


def _apply_check_none_visible(fields) -> None:
    for f in _picker_visible_fields(fields):
        st.session_state[f"llm_include_{f.row_index}"] = False


def _apply_check_all_fields(fields) -> None:
    _set_all_llm_include(fields, True)


def _apply_check_none_fields(fields) -> None:
    _set_all_llm_include(fields, False)


def _safe_display(text: str) -> str:
    """Prevent accidental LaTeX rendering in Streamlit markdown."""
    if not text:
        return ""
    return str(text).replace("$", "＄")


_PDF_CITATION_RE = re.compile(r"/([^/]+\.pdf)(?:#page=(\d+))?", re.IGNORECASE)


def _format_pdf_citation_label(url: str) -> str:
    """Short label for a spec PDF URL (filename + page)."""
    match = _PDF_CITATION_RE.search(url or "")
    if not match:
        return "Spec PDF"
    filename, page = match.group(1), match.group(2)
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    label = stem.replace("_", " ")
    if page:
        page_num = str(int(page))
        return f"{label} (page {page_num})"
    return label


def _format_section_citation_display(ref: str) -> str:
    """Normalize section reference text for display (drop trailing punctuation)."""
    cleaned = re.sub(r"\s+", " ", (ref or "").strip())
    return cleaned.rstrip(" -:–—.,;")


def _format_ui_citation_bullets_markdown(citations: list[str]) -> str:
    """Markdown bullets with readable labels; URLs stay in the link target."""
    lines: list[str] = []
    for cite in citations:
        text = str(cite).strip()
        if not text:
            continue
        if text.startswith("http"):
            label = _safe_display(_format_pdf_citation_label(text))
            lines.append(f"- [{label}]({text})")
        else:
            lines.append(f"- {_safe_display(_format_section_citation_display(text))}")
    return "\n".join(lines)


def _citations_for_result(result: dict) -> list[str]:
    """Citations stored on the result."""
    stored = result.get("citations")
    return list(stored) if stored else []


def _field_has_category_suffix(field) -> bool:
    cat = field.category.strip()
    label = field.display_label.strip()
    return bool(cat and cat.lower() not in label.lower())


def _field_category_plain(field) -> str:
    if not _field_has_category_suffix(field):
        return ""
    return f" ({field.category.strip()})"


def _field_category_markdown(field) -> str:
    if not _field_has_category_suffix(field):
        return ""
    return f" (*{_safe_display(field.category.strip())}*)"


def _field_expander_title(field) -> str:
    """Plain numbered title (expanders, logs); category in parentheses when needed."""
    label = field.display_label.strip()
    return f"Prompt {field.prompt_number}: {label}{_field_category_plain(field)}"


def _field_expander_title_markdown(field) -> str:
    """Markdown numbered title with italic category in parentheses."""
    label = _safe_display(field.display_label.strip())
    return f"**{field.prompt_number}. {label}**{_field_category_markdown(field)}"


def _field_prompt_label_markdown(field) -> str:
    """Markdown picker label with prompt number; italic category in parentheses."""
    label = _safe_display(field.display_label.strip())
    return f"**{field.prompt_number}. {label}**{_field_category_markdown(field)}"


def _raw_llm_answer_from_response(response: str) -> str:
    """Extract the model JSON ``answer`` field from a completion payload."""
    text = (response or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    answer = payload.get("answer", "")
    if isinstance(answer, str):
        return answer.strip()
    if answer is not None:
        return str(answer).strip()
    return ""


def _llm_answer_for_display(result: dict) -> str:
    """Answer string to show under Complete (parsed value or raw JSON answer)."""
    parsed = str(result.get("parsed_answer", "") or "").strip()
    if parsed:
        return parsed
    raw_answer = str(result.get("llm_answer_raw", "") or "").strip()
    if raw_answer:
        return raw_answer
    from_response = _raw_llm_answer_from_response(str(result.get("response", "") or ""))
    if from_response:
        return from_response
    return NOT_SPECIFIED if result.get("step") == "done" and result.get("response") else ""


def _format_checklist_answer_display(answer: str) -> str:
    """Human-readable gloss for common checklist answer shapes."""
    text = (answer or "").strip()
    if not text:
        return "_No value extracted._"
    m = re.match(r"^(\d{6})-(\d+)$", text)
    if m:
        section, page = m.group(1), m.group(2)
        sec_fmt = f"Section {section[:2]} {section[2:4]} {section[4:6]}"
        return (
            f"**{text}** — designated spec reference: **{sec_fmt}**, "
            f"document page **{page}**"
        )
    if re.search(r"\bSection\s+[\d][\d\s.\-/]*", text, re.IGNORECASE):
        return f"**{text}** — designated spec section reference(s)"
    return f"**{text}**"


def _short_display_text(text: str, max_chars: int = 120) -> str:
    """Compact text for one-line UI metadata."""
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def _field_possible_answers_display(field) -> str:
    """One-line display of the answer constraint shown to the LLM."""
    if field.allowed_options:
        return ", ".join(field.allowed_options)
    return (field.possible_answers_raw or "").strip() or "No constrained answers listed"


def _result_value_for_display(field, result: dict | None = None) -> str:
    """Return the current visible result for a review field or log entry."""
    if result is None:
        status = _field_status(field)
        value = _field_value(field)
    else:
        status = _autofill_result_state(result)
        value = str(result.get("parsed_answer", "") or "").strip()

    if status == CANNOT_ANSWER_FROM_KB:
        return FILTER_INFORMATION_NOT_FOUND_IN_KB
    if status == "error":
        return "Error"
    if status == "running":
        return "Processing"
    if value:
        return value
    return "Blank"


def _prompt_result_caption(field, result: dict | None = None) -> str:
    """Show prompt number, possible answers, and selected/extracted result."""
    possible_answers = _safe_display(_short_display_text(_field_possible_answers_display(field)))
    answer = _safe_display(_short_display_text(_result_value_for_display(field, result)))
    return (
        f"Prompt **{field.prompt_number}** · "
        f"Possible answers: `{possible_answers}` · "
        f"Result: **{answer}**"
    )


def _llm_field_help(field) -> str:
    """Plain-text tooltip for auto-fill picker checkboxes."""
    prompt = (field.prompt or "").strip()
    if len(prompt) > 400:
        prompt = prompt[:400] + "…"
    answers = (field.possible_answers_raw or "").strip() or "No constrained answers listed."
    if len(answers) > 200:
        answers = answers[:200] + "…"
    return (
        f"Prompt {field.prompt_number}: {prompt}\n\n"
        f"Possible answers: {answers}"
    )


def _field_matches_picker_filter(field, query: str, category: str) -> bool:
    if category != "All categories" and field.category != category:
        return False
    if not query:
        return True
    haystack = " ".join(
        (
            str(field.prompt_number),
            field.display_label,
            field.category,
            field.prompt or "",
        )
    ).lower()
    return query in haystack


def _picker_visible_fields(fields) -> list:
    query = str(st.session_state.get("cl_picker_search", "") or "").strip().lower()
    category = st.session_state.get("cl_picker_category", "All categories")
    return [
        f
        for f in fields
        if _field_matches_picker_filter(f, query, category)
    ]


def _picker_display_fields(fields) -> list:
    """Picker rows: current filter plus any field checked for auto-fill."""
    by_row: dict[int, object] = {f.row_index: f for f in _picker_visible_fields(fields)}
    for f in _selected_fields(fields):
        by_row.setdefault(f.row_index, f)
    return list(by_row.values())


def _render_llm_include_checkbox(field) -> None:
    """Picker row: checkbox and numbered label kept in one horizontal row."""
    with st.container(horizontal=True, gap="xxsmall", vertical_alignment="center"):
        st.checkbox(
            label=" ",
            key=f"llm_include_{field.row_index}",
            label_visibility="collapsed",
        )
        st.markdown(_field_prompt_label_markdown(field), help=_llm_field_help(field))


def _render_selection_summary(fields) -> None:
    counts = _autofill_selection_counts(fields)
    st.metric(
        "Selected for auto-fill",
        counts["selected"],
        help=(
            "Checked in the field picker. Rows already auto-filled are not processed "
            "again when you click **Auto-fill selected fields**."
        ),
    )
    if counts["selected"] == 0:
        st.caption("No fields selected. Open the picker and check prompts to queue.")
    elif counts["pending"] == 0:
        st.caption(
            f"All **{counts['selected']}** selected field(s) have been auto-filled."
        )
    else:
        st.caption(
            f"**{counts['pending']}** field(s) will run when you click "
            "**Auto-fill selected fields**."
        )


def _render_page_header() -> None:
    st.title("Spec. Compliance Review Checklist")
    st.caption(
        f"Auto-fill from uploaded specification PDF(s) via {CHECKLIST_DEFAULT_MODEL}"
    )
    if pdf_upload_in_progress() or pending_uploader_files():
        st.warning(
            "PDF upload is still in progress on **Home** — return and wait for it to finish."
        )
    names = st.session_state.get("loaded_pdf_names") or []
    if names:
        st.caption(f"Using PDF(s): **{', '.join(names)}**")
    elif not pdf_uploads_ready():
        st.warning("Upload specification PDF(s) on the **Home** page before auto-fill.")


def _render_page_metrics(fields) -> None:
    answered = sum(1 for f in fields if _is_answered(f))
    cannot_answer = sum(1 for f in fields if _field_status(f) == CANNOT_ANSWER_FROM_KB)
    review_count = sum(1 for f in fields if _has_review_warning(f))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Checklist fields", len(fields))
    c2.metric("Answered", answered)
    c3.metric(FILTER_INFORMATION_NOT_FOUND_IN_KB, cannot_answer)
    c4.metric("Needs review", review_count)

    log = st.session_state.get("checklist_autofill_log") or []
    if log:
        summary = _summarize_autofill_log(log)
        bits = [f"**{summary['answered']}** filled"]
        if summary[CANNOT_ANSWER_FROM_KB]:
            bits.append(
                f"**{summary[CANNOT_ANSWER_FROM_KB]}** "
                f"{FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()}"
            )
        if summary["review"]:
            bits.append(f"**{summary['review']}** need review")
        if summary["error"]:
            bits.append(f"**{summary['error']}** errors")
        st.caption("Last auto-fill: " + ", ".join(bits) + ".")


def _render_llm_controls(fields) -> None:
    with st.container(border=True):
        st.subheader("Auto-fill from specifications")

        counts = _autofill_selection_counts(fields)
        selected_count = counts["selected"]
        pending_count = counts["pending"]
        _render_selection_summary(fields)

        with st.expander("Choose fields to auto-fill", expanded=False, icon=":material/checklist:"):
            picker_categories = _category_filter_options(fields)
            if st.session_state.cl_picker_category not in picker_categories:
                st.session_state.cl_picker_category = "All categories"

            f1, f2 = st.columns([2, 1])
            with f1:
                st.text_input(
                    "Search prompts",
                    key="cl_picker_search",
                    placeholder="Label, category, prompt number, or prompt text",
                )
            with f2:
                st.selectbox(
                    "Picker category",
                    picker_categories,
                    key="cl_picker_category",
                )

            visible_picker = _picker_visible_fields(fields)
            display_picker = _picker_display_fields(fields)
            with st.container(horizontal=True):
                st.button(
                    "Check all visible",
                    key="cl_check_all",
                    on_click=_apply_check_all_visible,
                    args=(fields,),
                )
                st.button(
                    "Check none visible",
                    key="cl_check_none",
                    on_click=_apply_check_none_visible,
                    args=(fields,),
                )
                st.button(
                    "Check all",
                    key="cl_check_all_fields",
                    on_click=_apply_check_all_fields,
                    args=(fields,),
                )
                st.button(
                    "Check none",
                    key="cl_check_none_fields",
                    on_click=_apply_check_none_fields,
                    args=(fields,),
                )

            st.caption(
                f"Selected **{counts['selected']}** of **{len(fields)}** prompts"
            )
            if not display_picker:
                st.caption("No prompts match the search or category filter.")
            else:
                pinned_count = len(display_picker) - len(visible_picker)
                if pinned_count > 0 and visible_picker:
                    st.caption(
                        f"Also showing **{pinned_count}** selected field(s) from other "
                        "categories or search filters."
                    )
                elif pinned_count > 0:
                    st.caption(
                        f"Showing **{pinned_count}** selected field(s) outside the "
                        "current search or category filter."
                    )
                for f in _sort_fields_by_prompt_number(display_picker):
                    _render_llm_include_checkbox(f)

        if st.button(
            "Auto-fill selected fields",
            type="primary",
            key="cl_autofill",
            width="stretch",
            disabled=selected_count == 0 or pending_count == 0,
        ):
            _run_autofill(fields)

        if st.session_state.get("checklist_autofill_log"):
            _render_autofill_log()


def _render_search_details(result: dict) -> None:
    st.markdown("**:material/description: Specification references**")
    citations = _citations_for_result(result)
    if citations:
        st.markdown(_safe_display(_format_ui_citation_bullets_markdown(citations)))
    else:
        st.caption("_No section/page citations recorded for this field._")
    if not (result.get("search_terms") or "").strip() and not (
        result.get("retrieved_context") or ""
    ).strip():
        st.caption("Answers come from direct PDF review (no separate search step).")
    prompt = (result.get("prompt") or "").strip()
    if prompt:
        with st.expander("Checklist prompt", expanded=False):
            st.markdown(_safe_display(prompt))


def _render_technical_details(result: dict) -> None:
    """Model, constraints, and raw LLM payload (in progress or collapsed when done)."""
    st.markdown("**:material/smart_toy: LLM extraction**")
    st.caption(f"Model: `{result.get('model', '')}`")
    if result.get("step") == "llm":
        st.caption("Extracting answer from the uploaded specification PDF…")
    allowed = (result.get("possible_answers") or "").strip()
    if allowed:
        st.caption("Allowed answers sent to the model")
        st.code(allowed, language=None)
    raw = (result.get("response") or "").strip()
    if raw:
        st.caption("Raw model response (JSON)")
        with st.container(border=True):
            st.text(_safe_display(raw))


def _render_complete_status_row(result: dict, field=None) -> None:
    """Complete label and LLM answer on one row (auto-fill progress expanders)."""
    status_col, answer_col = st.columns([1, 6], vertical_alignment="center")
    with status_col:
        st.caption(":material/check_circle: Complete")
    with answer_col:
        err = (result.get("error") or "").strip()
        if err:
            st.error(_safe_display(err))
            return

        answer = _llm_answer_for_display(result)
        llm_expl = _result_llm_text(result)
        parts: list[str] = []
        if answer:
            parts.append(_format_checklist_answer_display(_safe_display(answer)))
        if llm_expl:
            parts.append(_safe_display(llm_expl))
        if parts:
            st.markdown(" — ".join(parts))
        else:
            st.markdown("_No value returned by the model._")


def _answer_summary_has_details(result: dict, field=None) -> bool:
    """True when secondary captions or warnings should appear below the status row."""
    parsed = str(result.get("parsed_answer", "") or "").strip()
    if result.get("parse_valid") is not False and not is_checklist_answered_value(parsed):
        return True
    llm_expl = _result_llm_text(result)
    if not llm_expl:
        footnote = result.get("footnote_explanation") or ""
        if not footnote and field is not None:
            footnote = compose_llm_footnote(
                field,
                parsed,
                llm_expl,
                result.get("retrieved_context", ""),
                status=result.get("field_status", ""),
                status_reason=result.get("status_reason", ""),
                search_explanation=_result_llm_text(result),
            )
        if footnote:
            return True
    if (result.get("parse_note") or "").strip():
        return True
    if result.get("parse_valid") is False and not (result.get("error") or "").strip():
        return True
    return False


def _render_answer_summary_details(result: dict, field=None) -> None:
    """Footnotes and parser warnings below the Complete + answer row."""
    parsed = str(result.get("parsed_answer", "") or "").strip()
    if result.get("parse_valid") is not False and not is_checklist_answered_value(parsed):
        st.caption(
            f"Recorded as **{FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()}**. "
            "The answer cell stays blank; this row appears in the footnotes appendix."
        )

    llm_expl = _result_llm_text(result)
    if not llm_expl:
        footnote = result.get("footnote_explanation") or ""
        if not footnote and field is not None:
            footnote = compose_llm_footnote(
                field,
                parsed,
                llm_expl,
                result.get("retrieved_context", ""),
                status=result.get("field_status", ""),
                status_reason=result.get("status_reason", ""),
                search_explanation=_result_llm_text(result),
            )
        if footnote:
            st.caption("How this value is supported")
            st.markdown(_safe_display(footnote))

    note = result.get("parse_note") or ""
    if note:
        st.caption(f"Parser note: {_safe_display(note)}")
    if result.get("parse_valid") is False and not (result.get("error") or "").strip():
        st.warning("Review this value against the allowed answers before export.")


def _render_autofill_log_entry(field, result: dict, index: int, *, expanded: bool) -> None:
    state = _autofill_result_state(result)
    if state == "error":
        status = ":red-badge[error]"
    elif state == "answered":
        status = ":green-badge[answered]"
    elif state == CANNOT_ANSWER_FROM_KB:
        status = ":blue-badge[not in spec]"
    elif state == "review":
        status = ":orange-badge[review]"
    else:
        status = ":blue-badge[running]"
    title = f"{_field_expander_title(field)} {status}"
    with st.expander(title, expanded=expanded):
        st.caption(_prompt_result_caption(field, result))
        _render_complete_status_row(result, field)
        if _answer_summary_has_details(result, field):
            _render_answer_summary_details(result, field)
        _render_search_details(result)
        if result.get("response"):
            with st.expander("Technical details (raw model response)", expanded=False):
                _render_technical_details(result)


def _format_autofill_log_meta_caption(meta: dict) -> str:
    """One-line summary of the last auto-fill run."""
    parts = [
        f"Model `{meta.get('model', '')}`",
        f"**{meta.get('completed', 0)}** / **{meta.get('total', 0)}** fields",
    ]
    batch_size = meta.get("batch_size")
    if batch_size:
        parts.append(f"batches of **{batch_size}**")
    elapsed = meta.get("elapsed_seconds")
    if elapsed is not None:
        parts.append(f"**{elapsed:.0f}s** total")
    return " · ".join(parts)


def _render_autofill_log() -> None:
    log = st.session_state.get("checklist_autofill_log") or []
    if not log:
        return
    meta = st.session_state.get("checklist_autofill_meta") or {}
    summary = _summarize_autofill_log(log)
    st.subheader("Last auto-fill run")
    if meta:
        st.caption(_format_autofill_log_meta_caption(meta))
    st.caption(
        f"Filled **{summary['answered']}** · "
        f"{FILTER_INFORMATION_NOT_FOUND_IN_KB} **{summary[CANNOT_ANSWER_FROM_KB]}** · "
        f"Needs review **{summary['review']}** · "
        f"Errors **{summary['error']}**"
    )
    st.caption(
        "Expand a row for the checklist answer, specification citations, and LLM details."
    )
    for i, entry in enumerate(log):
        field_idx = entry.get("row_index")
        field = next((f for f in st.session_state.get("_checklist_fields_cache", []) if f.row_index == field_idx), None)
        if field is None:
            with st.expander(f"{i + 1}. Row {field_idx}", expanded=False):
                st.json(entry)
            continue
        state = _autofill_result_state(entry)
        expand = state in {"review", "error"}
        _render_autofill_log_entry(field, entry, i, expanded=expand)


def _apply_autofill_result(field, result: dict) -> None:
    """Write one batch result into session state and form widgets."""
    _clear_row_user_override(field.row_index)
    parsed = str(result.get("parsed_answer", "") or "").strip()
    parse_valid = result.get("parse_valid")
    parse_note = result.get("parse_note") or ""
    field_status = result.get("field_status", "")
    status_reason = result.get("status_reason", "")
    llm_expl = _result_llm_text(result)
    footnote_explanation = compose_llm_footnote(
        field,
        parsed,
        llm_expl,
        "",
        status=field_status,
        status_reason=status_reason,
        search_explanation="",
    )
    result["footnote_explanation"] = footnote_explanation

    if not parse_valid:
        st.session_state.checklist_values[field.row_index] = parsed
        _sync_field_widget_from_value(field, parsed)
        _set_field_status(
            field.row_index, "needs_review", reason=parse_note or "parse failed", result=result
        )
        st.session_state[f"checklist_parse_warn_{field.row_index}"] = parse_note or "parse failed"
    elif field_status == CANNOT_ANSWER_FROM_KB:
        st.session_state.checklist_values[field.row_index] = ""
        _sync_field_widget_from_value(field, "")
        _set_field_status(field.row_index, CANNOT_ANSWER_FROM_KB, reason=status_reason, result=result)
        st.session_state.pop(f"checklist_parse_warn_{field.row_index}", None)
    else:
        st.session_state.checklist_values[field.row_index] = parsed
        _sync_field_widget_from_value(field, parsed)
        _set_field_status(field.row_index, "answered", result=result)
        st.session_state.pop(f"checklist_parse_warn_{field.row_index}", None)
    _store_footnote_meta(field.row_index, result)


def _openai_api_key_configured() -> bool:
    try:
        return bool(resolve_openai_api_key())
    except RuntimeError:
        return False


def _format_autofill_batch_status_markdown(
    batch_idx: int,
    total_batches: int,
    batch: list,
    *,
    max_label_len: int = 72,
) -> str:
    """Batch header and concise numbered prompt list for auto-fill progress."""
    header = (
        f"**Batch {batch_idx + 1}/{total_batches}** — "
        f"prompts **{batch[0].prompt_number}**–**{batch[-1].prompt_number}** "
        f"({len(batch)} field(s))"
    )
    items: list[str] = []
    for field in batch:
        label = _safe_display(field.display_label.strip())
        if len(label) > max_label_len:
            label = label[: max_label_len - 1] + "…"
        items.append(f"- **{field.prompt_number}.** {label}")
    return header + "\n\n" + "\n".join(items)


def _format_autofill_error_message(exc: Exception) -> str:
    """User-facing message for a failed batch API call."""
    return format_llm_error(exc)[:500]


def _autofill_preflight_ok() -> bool:
    """Require indexed PDFs and OPENAI_API_KEY before starting auto-fill."""
    if pdf_upload_in_progress() or pending_uploader_files():
        st.error(
            "PDF upload is still in progress on the **Home** page — return and wait for it to finish."
        )
        return False
    if not loaded_spec_documents():
        st.error("Upload at least one specification PDF on the **Home** page first.")
        return False
    if not _openai_api_key_configured():
        st.error(
            "OPENAI_API_KEY is not configured. Add it to `.streamlit/secrets.toml` "
            "and refresh the page."
        )
        return False
    return True


def _run_autofill(fields) -> None:
    selected = [f for f in fields if st.session_state.get(f"llm_include_{f.row_index}", False)]
    if not selected:
        st.warning("No fields selected for auto-fill.")
        return

    to_process = _fields_to_autofill(selected)
    if not to_process:
        return

    if not _autofill_preflight_ok():
        return

    documents = loaded_spec_documents()
    model = st.session_state.checklist_selected_model
    st.session_state["_checklist_fields_cache"] = fields
    st.session_state.checklist_autofill_log = []
    run_started = time.monotonic()
    st.session_state.checklist_autofill_meta = {
        "model": model,
        "batch_size": CHECKLIST_BATCH_SIZE,
        "total": len(to_process),
        "completed": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    batches = [
        to_process[i : i + CHECKLIST_BATCH_SIZE]
        for i in range(0, len(to_process), CHECKLIST_BATCH_SIZE)
    ]

    progress_ph = st.empty()
    with progress_ph.container():
        st.subheader("Auto-fill progress")
        st.caption(
            f"Processing **{len(to_process)}** field(s) in **{len(batches)}** batch(es) "
            f"with **{model}**"
        )
        overall = st.progress(0.0, text="Starting auto-fill…")
        batch_status_ph = st.empty()
        completed = 0
        for batch_idx, batch in enumerate(batches):
            batch_status_ph.markdown(
                _format_autofill_batch_status_markdown(batch_idx, len(batches), batch)
            )
            overall.progress(
                completed / len(to_process),
                text=(
                    f"Sending batch {batch_idx + 1}/{len(batches)} — "
                    f"{len(batch)} prompt(s) to {model}"
                ),
            )
            try:
                results = run_checklist_batch_autofill(batch, documents, model=model)
                for field, result in zip(batch, results):
                    _apply_autofill_result(field, result)
                    st.session_state.checklist_autofill_log.append(result)
                    completed += 1
            except Exception as e:
                err_msg = _format_autofill_error_message(e)
                for field in batch:
                    failed = {
                        "row_index": field.row_index,
                        "category": field.category,
                        "prompt": field.prompt,
                        "possible_answers": field.possible_answers_raw,
                        "field_kind": field.field_kind,
                        "model": model,
                        "step": "done",
                        "error": err_msg,
                        "parse_valid": False,
                        "parse_note": err_msg[:120],
                        "field_status": "error",
                        "status_reason": err_msg[:120],
                    }
                    st.session_state.checklist_autofill_log.append(failed)
                    _set_field_status(field.row_index, "error", reason=err_msg[:120], result=failed)
                    st.session_state[f"checklist_parse_warn_{field.row_index}"] = err_msg[:120]
                    completed += 1
            st.session_state.checklist_autofill_meta["completed"] = completed
            overall.progress(
                completed / len(to_process),
                text=f"Completed {completed} / {len(to_process)} fields",
            )
        summary = _summarize_autofill_log(st.session_state.checklist_autofill_log)
        result_bits = [
            f"**{summary['answered']}** answered",
            f"**{summary[CANNOT_ANSWER_FROM_KB]}** {FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()}",
        ]
        if summary["review"]:
            result_bits.append(f"**{summary['review']}** needs review")
        if summary["error"]:
            result_bits.append(f"**{summary['error']}** error")

        message = "Auto-fill complete — " + ", ".join(result_bits) + "."
        if summary["error"] or summary["review"]:
            st.warning(message)
        elif summary["answered"]:
            st.success(message)
        else:
            st.info(
                message
                + " The last run is shown below and recorded in the footnotes appendix."
            )

        st.session_state.checklist_autofill_meta["finished_at"] = datetime.now(
            timezone.utc
        ).isoformat()
        st.session_state.checklist_autofill_meta["elapsed_seconds"] = (
            time.monotonic() - run_started
        )

    progress_ph.empty()
    st.session_state["cl_status_filter"] = "Last auto-fill"
    st.session_state["_checklist_show_last_run_hint"] = True


def _on_choice_toggle(row_index: int, option: str, all_options: list[str]) -> None:
    key = f"cl_ch_{row_index}_{option}"
    if st.session_state.get(key):
        st.session_state.checklist_values[row_index] = option
        _set_field_status(row_index, "answered")
        st.session_state.pop(f"checklist_parse_warn_{row_index}", None)
        _clear_footnote_meta_for_row(row_index)
        for opt in all_options:
            if opt != option:
                st.session_state[f"cl_ch_{row_index}_{opt}"] = False
    elif st.session_state.checklist_values.get(row_index) == option:
        st.session_state.checklist_values[row_index] = ""
        st.session_state.checklist_field_status.pop(row_index, None)
        _clear_footnote_meta_for_row(row_index)


def _choice_widget(field) -> None:
    stored = st.session_state.checklist_values.get(field.row_index, "")
    for opt in field.allowed_options:
        st.session_state[f"cl_ch_{field.row_index}_{opt}"] = stored == opt

    cols = st.columns(min(len(field.allowed_options), 4) or 1)
    for i, opt in enumerate(field.allowed_options):
        with cols[i % len(cols)]:
            ck = f"cl_ch_{field.row_index}_{opt}"
            st.checkbox(
                opt,
                key=ck,
                on_change=_on_choice_toggle,
                args=(field.row_index, opt, field.allowed_options),
            )


def _text_widget(field) -> None:
    val = st.session_state.checklist_values.get(field.row_index, "")
    if field.field_kind == "notes":
        wk = f"cl_ta_{field.row_index}"
        if wk not in st.session_state:
            st.session_state[wk] = str(val) if val else ""
        st.text_area(format_prompt_label(field), key=wk)
        st.session_state.checklist_values[field.row_index] = st.session_state[wk]
        if st.session_state[wk] != val:
            st.session_state.pop(f"checklist_parse_warn_{field.row_index}", None)
            _clear_footnote_meta_for_row(field.row_index)
            if is_checklist_answered_value(st.session_state[wk]):
                _set_field_status(field.row_index, "answered")
            else:
                st.session_state.checklist_field_status.pop(field.row_index, None)
    else:
        wk = f"cl_ti_{field.row_index}"
        if wk not in st.session_state:
            st.session_state[wk] = str(val) if val else ""
        st.text_input(format_prompt_label(field), key=wk)
        st.session_state.checklist_values[field.row_index] = st.session_state[wk]
        if st.session_state[wk] != val:
            st.session_state.pop(f"checklist_parse_warn_{field.row_index}", None)
            _clear_footnote_meta_for_row(field.row_index)
            if is_checklist_answered_value(st.session_state[wk]):
                _set_field_status(field.row_index, "answered")
            else:
                st.session_state.checklist_field_status.pop(field.row_index, None)


def _field_status_caption(field) -> str | None:
    """Short guidance under each review row based on auto-fill / manual state."""
    warn = st.session_state.get(f"checklist_parse_warn_{field.row_index}")
    if warn:
        return f"Needs review: {warn}. Edit the value or **Clear** to reset the row."

    field_status = _field_status(field)
    entry = _field_status_entry(field.row_index)
    latest = _latest_autofill_entry_for_row(field.row_index)

    if field_status == CANNOT_ANSWER_FROM_KB:
        msg = (
            "No supported answer in specifications. The cell stays blank; "
            "details appear in **Footnotes and citations** and exports."
        )
        reason = (entry.get("reason") or "").strip()
        return f"{msg} {reason}".strip() if reason else msg

    if field_status == "error":
        reason = (entry.get("reason") or "Processing failed.").strip()
        return f"Auto-fill failed: {reason}. **Clear** the row or edit the value manually."

    if latest and field_status == "answered":
        return "Filled by auto-fill. Edit below as needed."

    if (
        st.session_state.get(f"llm_include_{field.row_index}", False)
        and field_status == "unanswered"
        and not _is_answered(field)
    ):
        return "Checked for auto-fill. Run **Auto-fill selected fields** above to process."

    return None


def _render_field_row(field) -> None:
    warn = st.session_state.get(f"checklist_parse_warn_{field.row_index}")
    value = _field_value(field)
    field_status = _field_status(field)
    if field_status == "answered":
        status = ":green-badge[answered]"
    elif field_status == CANNOT_ANSWER_FROM_KB:
        status = ":blue-badge[not in spec]"
    elif field_status == "error":
        status = ":red-badge[error]"
    else:
        status = ":blue-badge[blank]"
    if warn:
        status += " :orange-badge[review]"

    label_col, action_col = st.columns([5, 1], vertical_alignment="top")
    with label_col:
        st.markdown(f"{_field_expander_title_markdown(field)} {status}")
        st.caption(_prompt_result_caption(field))
    with action_col:
        st.button(
            "Clear",
            key=f"cl_clear_{field.row_index}",
            on_click=_clear_field_value,
            args=(field,),
            width="stretch",
            disabled=not value and not warn and field_status == "unanswered",
        )

    status_caption = _field_status_caption(field)
    if status_caption:
        st.caption(status_caption)
    elif warn:
        st.caption(f":orange[{warn}]")
    if field.field_kind == "choice" and field.allowed_options:
        _choice_widget(field)
    else:
        _text_widget(field)


def _filter_visible_fields(fields) -> list:
    categories = _category_filter_options(fields)
    status_options = [
        "All fields",
        "Last auto-fill",
        FILTER_PROCESSED_BY_AUTOFILL,
        FILTER_ANSWERED_BY_LLM,
        "Needs review",
        FILTER_INFORMATION_NOT_FOUND_IN_KB,
        FILTER_NOT_PROCESSED,
    ]
    if st.session_state.cl_category_filter not in categories:
        st.session_state.cl_category_filter = "All categories"
    legacy_status = _LEGACY_STATUS_FILTER_LABELS.get(st.session_state.get("cl_status_filter", ""))
    if legacy_status:
        st.session_state.cl_status_filter = legacy_status
    if st.session_state.cl_status_filter not in status_options:
        st.session_state.cl_status_filter = "All fields"

    c1, c2 = st.columns([1, 2])
    with c1:
        category = st.selectbox(
            "Category",
            categories,
            key="cl_category_filter",
        )
    with c2:
        status = st.segmented_control(
            "Review status",
            status_options,
            key="cl_status_filter",
        )
        status = status or "All fields"

    visible = list(fields)
    if category != "All categories":
        visible = [f for f in visible if f.category == category]
    if status == FILTER_PROCESSED_BY_AUTOFILL:
        visible = [f for f in visible if _is_processed_by_llm(f)]
    elif status == FILTER_ANSWERED_BY_LLM:
        visible = [f for f in visible if _is_answered_by_llm(f)]
    elif status == "Needs review":
        visible = [f for f in visible if _has_review_warning(f)]
    elif status == "Last auto-fill":
        last_rows = _last_autofill_row_indices()
        visible = [f for f in visible if f.row_index in last_rows]
    elif status == FILTER_INFORMATION_NOT_FOUND_IN_KB:
        visible = [f for f in visible if _field_status(f) == CANNOT_ANSWER_FROM_KB]
    elif status == FILTER_NOT_PROCESSED:
        visible = [f for f in visible if _field_status(f) == "unanswered"]
    return visible


def _render_last_run_filter_hint(fields) -> None:
    if not st.session_state.pop("_checklist_show_last_run_hint", False):
        return
    last_rows = _last_autofill_row_indices()
    if not last_rows:
        return
    answered_last = sum(1 for f in fields if f.row_index in last_rows and _is_answered(f))
    if answered_last:
        message = (
            "Review list filtered to **Last auto-fill**. Check answers below; "
            "open **Last auto-fill run** above for retrieval and LLM details."
        )
    else:
        message = (
            "Review list filtered to **Last auto-fill**. Prompts marked "
            f"**{FILTER_INFORMATION_NOT_FOUND_IN_KB}** leave answer cells blank but "
            "appear in footnotes and exports."
        )
    st.info(message, icon=":material/info:")


def _empty_review_message() -> str:
    status = st.session_state.get("cl_status_filter", "All fields")
    log = st.session_state.get("checklist_autofill_log") or []
    summary = _summarize_autofill_log(log) if log else {}
    if status == FILTER_PROCESSED_BY_AUTOFILL and summary.get(CANNOT_ANSWER_FROM_KB) and not summary.get("answered"):
        return (
            f"No filled cells from the last run. Auto-fill returned **{FILTER_INFORMATION_NOT_FOUND_IN_KB}** "
            "for those prompts; switch to **Last auto-fill** to inspect them."
        )
    if status == "Needs review":
        return "No fields are flagged **Needs review**."
    if status == FILTER_ANSWERED_BY_LLM:
        return f"No fields match **{FILTER_ANSWERED_BY_LLM}** (valid auto-fill answers only)."
    if status == FILTER_INFORMATION_NOT_FOUND_IN_KB:
        return f"No fields have been recorded as **{FILTER_INFORMATION_NOT_FOUND_IN_KB}**."
    if status == FILTER_NOT_PROCESSED:
        return f"No fields are **{FILTER_NOT_PROCESSED.lower()}**."
    if status == "Last auto-fill" and not log:
        return "No auto-fill run is available yet."
    return "No fields match the current filters."


def _render_form_body(fields) -> None:
    with st.container(border=True):
        st.subheader("Review checklist")
        st.caption(
            "Edit answers below. **Processed by auto-fill** includes filled cells, "
            f"{FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()}, and parse-review rows."
        )
        visible_fields = _filter_visible_fields(fields)
        _render_last_run_filter_hint(fields)
        st.caption(f"Showing **{len(visible_fields)}** of **{len(fields)}** fields.")
        if not visible_fields:
            st.info(_empty_review_message(), icon=":material/filter_alt_off:")
            return

        for f in _sort_fields_by_prompt_number(visible_fields):
            _render_field_row(f)


def _render_footnotes_preview(fields) -> None:
    values = st.session_state.get("checklist_values", {})
    export_values_dict = _values_for_export(values, fields)
    footnote_meta = _footnote_meta_for_export()
    footnotes = build_footnotes_for_export(fields, export_values_dict, footnote_meta)
    if not footnotes:
        st.caption(
            f"Footnotes are generated for answered fields and {FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()} "
            "results after auto-fill. They appear at the bottom of exported Markdown and Word files."
        )
        return
    with st.expander(f"Footnotes and citations ({len(footnotes)} prompts)", expanded=False):
        for fn in footnotes:
            st.markdown(f"#### {fn.number}. {_safe_display(fn.label)}")
            label = "Status" if fn.status == CANNOT_ANSWER_FROM_KB else "Answer"
            st.markdown(f"**{label} recorded:** {_safe_display(fn.answer)}")
            st.markdown(_safe_display(fn.explanation))
            if fn.citations:
                st.markdown(_safe_display(format_citations_markdown(fn.citations)))


def _fields_cache_signature(fields) -> tuple:
    return tuple(
        (f.row_index, f.prompt_number, f.field_kind)
        for f in fields
    )


def _footnote_meta_cache_payload(meta: dict) -> str:
    slim: dict[str, dict] = {}
    for k, v in meta.items():
        if not isinstance(v, dict):
            continue
        slim[str(k)] = {
            "status": v.get("status", ""),
            "status_reason": v.get("status_reason", ""),
            "footnote_explanation": v.get("footnote_explanation", ""),
            "citations": v.get("citations", []),
        }
    return json.dumps(slim, sort_keys=True)


def _template_mtime(path: str) -> float:
    p = Path(path)
    return p.stat().st_mtime if p.is_file() else 0.0


def _template_exists(path: str) -> bool:
    return Path(path).is_file()


@st.cache_data(show_spinner=False)
def _cached_export_docx(
    fields_sig: tuple,
    values_key: tuple,
    footnote_meta_payload: str,
    template_path: str,
    template_mtime: float,
) -> bytes | None:
    fields = get_active_checklist_fields()
    values = dict(values_key)
    raw_meta = json.loads(footnote_meta_payload)
    footnote_meta = {int(k): v for k, v in raw_meta.items()}
    return export_checklist_docx(
        fields,
        values,
        template_path=template_path,
        footnote_meta=footnote_meta,
    )


def _export_full_markdown_help() -> str:
    return (
        "Full checklist in Markdown: every prompt by category, with answered and "
        "unanswered rows, plus a footnotes appendix for answered and "
        f"{FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()} prompts."
    )


def _export_answered_markdown_help() -> str:
    return (
        "Answered prompts only: rows with a filled value (not blank or Not specified), "
        "with matching footnotes."
    )


def _export_kb_not_found_markdown_help() -> str:
    return (
        f"Only prompts marked **{FILTER_INFORMATION_NOT_FOUND_IN_KB}** after auto-fill, "
        "with status and footnotes. Unanswered and answered-only rows are omitted."
    )


def _export_word_help() -> str:
    return (
        "Fills the official Hays Spec. Review Checklist Word template with current "
        "values (checkboxes and mapped text fields) and footnotes for answered and "
        f"{FILTER_INFORMATION_NOT_FOUND_IN_KB.lower()} prompts."
    )


def _render_export_download_buttons(
    docx_bytes: bytes | None,
    md_bytes_full: bytes,
    md_bytes_answered: bytes,
    md_bytes_kb_not_found: bytes,
) -> None:
    """One horizontal row: button + tooltip per export, minimal gap, left-aligned."""
    with st.container(
        horizontal=True,
        gap="xxsmall",
        vertical_alignment="center",
        width="content",
    ):
        if docx_bytes:
            st.download_button(
                "Word (.docx)",
                data=docx_bytes,
                file_name="spec_compliance_checklist.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="cl_dl_docx",
                type="primary",
                width="content",
                icon=":material/download:",
            )
            st.markdown("", help=_export_word_help())
        else:
            st.caption("Word export unavailable (template not found).")
        st.download_button(
            "Markdown (full)",
            data=md_bytes_full,
            file_name="spec_compliance_checklist.md",
            mime="text/markdown",
            key="cl_dl_md_full",
            type="primary",
            width="content",
            icon=":material/download:",
        )
        st.markdown("", help=_export_full_markdown_help())
        st.download_button(
            "Markdown (answered)",
            data=md_bytes_answered,
            file_name="spec_compliance_checklist_answered.md",
            mime="text/markdown",
            key="cl_dl_md_answered",
            type="primary",
            width="content",
            icon=":material/download:",
        )
        st.markdown("", help=_export_answered_markdown_help())
        st.download_button(
            "Markdown (not in specification)",
            data=md_bytes_kb_not_found,
            file_name="spec_compliance_checklist_not_in_kb.md",
            mime="text/markdown",
            key="cl_dl_md_kb_not_found",
            type="primary",
            width="content",
            icon=":material/download:",
        )
        st.markdown("", help=_export_kb_not_found_markdown_help())


def _render_downloads(fields) -> None:
    values = st.session_state.get("checklist_values", {})
    export_values_dict = _values_for_export(values, fields)
    footnote_meta = _footnote_meta_for_export()
    md_bytes_full = export_checklist_markdown(
        fields, export_values_dict, footnote_meta, export_filter="all"
    ).encode("utf-8")
    md_bytes_answered = export_checklist_markdown(
        fields, export_values_dict, footnote_meta, export_filter="answered"
    ).encode("utf-8")
    md_bytes_kb_not_found = export_checklist_markdown(
        fields, export_values_dict, footnote_meta, export_filter="kb_not_found"
    ).encode("utf-8")
    withheld = _export_withheld_count(fields)
    with st.container(border=True):
        st.subheader("Export")
        if withheld:
            st.caption(
                f"**{withheld}** field(s) with **Needs review** are omitted from downloads "
                "until corrected or cleared."
            )
        template_exists = _template_exists(CHECKLIST_TEMPLATE_DOCX)
        if template_exists:
            _render_export_alignment_warnings(fields, values)
        docx_bytes = _cached_export_docx(
            _fields_cache_signature(fields),
            tuple(sorted(export_values_dict.items())),
            _footnote_meta_cache_payload(footnote_meta),
            CHECKLIST_TEMPLATE_DOCX,
            _template_mtime(CHECKLIST_TEMPLATE_DOCX),
        )
        _render_export_download_buttons(
            docx_bytes,
            md_bytes_full,
            md_bytes_answered,
            md_bytes_kb_not_found,
        )


def _render_footnotes_section(fields) -> None:
    with st.container(border=True):
        st.subheader("Footnotes and citations")
        _render_footnotes_preview(fields)


def main() -> None:
    st.set_page_config(
        page_title="Spec Checklist",
        page_icon="img/favicon.ico",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not render_guest_email_gate():
        st.stop()
    inject_gpt_inspired_layout_css()

    init_pdf_session_state()
    fields = get_active_checklist_fields()
    st.session_state["_checklist_fields_cache"] = fields
    _init_session_state(fields)
    _render_tools_sidebar()

    _render_page_header()
    _render_llm_controls(fields)
    _render_page_metrics(fields)

    form_ph = st.empty()
    with form_ph.container():
        _render_form_body(fields)

    _render_footnotes_section(fields)
    _render_downloads(fields)


if __name__ == "__main__":
    main()
