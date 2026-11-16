"""Streamlit UI helpers for PDF upload/indexing and retrieval-grounded OpenAI chat."""

from __future__ import annotations

import re
from typing import Any, Generator

import streamlit as st

from utils.config_vars import CHAT_MODELS, DEFAULT_CHAT_MODEL, system_prompt
from utils.llm_errors import format_llm_error
from utils.llm_prompts import build_chat_user_message
from utils.openai_client import get_cached_openai_client, stream_chat_reply
from utils.retrieval import format_excerpts, retrieve_for_chat
from utils.text_cleanup import clean_llm_response
from utils.vector_store import delete_unfinished_documents, ingest_pdf, list_unfinished_documents

HISTORY_MAX_MESSAGES = 8
HISTORY_MAX_CHARS = 6000
FOLLOWUP_MAX_WORDS = 12
FOLLOWUP_MARKER = "this conversation"
CITATION_SUFFIX_MARKER = " When you cite specification sections or pages"


def init_pdf_session_state() -> None:
    """Initialize Streamlit session keys for uploaded, indexed specification PDFs."""
    st.session_state.setdefault("spec_documents_by_upload", {})
    st.session_state.setdefault("pdf_viewer_files_by_upload", {})
    loaded_names = st.session_state.setdefault("loaded_pdf_names", [])
    if loaded_names:
        deduped = list(dict.fromkeys(name for name in loaded_names if name))
        if deduped != loaded_names:
            st.session_state.loaded_pdf_names = deduped
    st.session_state.setdefault("processed_pdf_names", set())
    st.session_state.setdefault("failed_pdf_uploads", [])
    st.session_state.setdefault("pdf_upload_in_progress", False)


def loaded_spec_documents() -> list[dict[str, Any]]:
    """Indexed documents for the PDFs currently attached, in upload order."""
    by_upload = st.session_state.get("spec_documents_by_upload") or {}
    return [by_upload[name] for name in st.session_state.get("loaded_pdf_names") or [] if name in by_upload]


def _uploader_widget_key() -> str:
    """Versioned key so the uploader can reset without writing widget session state."""
    st.session_state.setdefault("spec_pdf_uploader_reset", 0)
    return f"spec_pdf_uploader_{st.session_state.spec_pdf_uploader_reset}"


def _bump_uploader_widget_key() -> None:
    st.session_state.spec_pdf_uploader_reset = (
        int(st.session_state.get("spec_pdf_uploader_reset") or 0) + 1
    )


def _uploader_files_from_state() -> list[Any]:
    raw = st.session_state.get(_uploader_widget_key())
    if not raw:
        return []
    files = raw if isinstance(raw, list) else [raw]
    return _dedupe_uploader_files(files)


def _dedupe_uploader_files(files: list[Any]) -> list[Any]:
    """Keep one UploadedFile per filename (Streamlit can retain duplicates on rerun)."""
    seen: set[str] = set()
    unique: list[Any] = []
    for uploaded_file in files:
        name = getattr(uploaded_file, "name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(uploaded_file)
    return unique


def pdf_upload_in_progress() -> bool:
    return bool(st.session_state.get("pdf_upload_in_progress"))


def pending_uploader_files() -> list[Any]:
    init_pdf_session_state()
    processed = st.session_state.setdefault("processed_pdf_names", set())
    return [f for f in _uploader_files_from_state() if f.name not in processed]


def pdf_uploads_ready() -> bool:
    if pdf_upload_in_progress() or pending_uploader_files():
        return False
    return bool(loaded_spec_documents())


def pdf_checklist_nav_disabled() -> bool:
    """True while Spec Checklist should stay unavailable (upload pending or incomplete)."""
    return not pdf_uploads_ready()


def _forget_removed_pdfs(removed_names: set[str]) -> None:
    """Drop removed PDFs from the session; their stored vectors stay for later reuse."""
    by_upload = st.session_state.get("spec_documents_by_upload") or {}
    viewer_files = st.session_state.get("pdf_viewer_files_by_upload") or {}
    for name in removed_names:
        by_upload.pop(name, None)
        viewer_files.pop(name, None)
    st.session_state.spec_documents_by_upload = by_upload
    st.session_state.pdf_viewer_files_by_upload = viewer_files
    if st.session_state.get("selected_citation_pdf") in removed_names:
        st.session_state.pop("selected_citation_pdf", None)
        st.session_state.pop("selected_citation_page", None)


def _sync_removed_uploaded_pdfs(uploaded_files: list[Any]) -> None:
    """Mirror file_uploader removals in the PDF session state."""
    loaded_names = list(st.session_state.get("loaded_pdf_names") or [])
    # file_uploader is empty when another page is shown; keep session PDFs across navigation.
    if not loaded_names or not uploaded_files:
        return
    uploaded_names = {uploaded_file.name for uploaded_file in uploaded_files}
    removed_names = set(loaded_names) - uploaded_names
    if not removed_names:
        return
    _forget_removed_pdfs(removed_names)
    st.session_state.loaded_pdf_names = [name for name in loaded_names if name in uploaded_names]
    st.session_state.processed_pdf_names = set(st.session_state.loaded_pdf_names)
    st.session_state.failed_pdf_uploads = [
        name for name in st.session_state.get("failed_pdf_uploads", []) if name in uploaded_names
    ]


def _show_upload_feedback() -> None:
    added = st.session_state.pop("pdf_upload_last_added", None) or []
    failed = st.session_state.pop("pdf_upload_last_failed", None) or []
    failed_details = st.session_state.pop("pdf_upload_last_failed_details", None) or {}
    for name in added:
        doc = (st.session_state.get("spec_documents_by_upload") or {}).get(name)
        if doc:
            note = "reused stored index" if doc.get("reused") else "indexed"
            st.toast(f"{name}: {doc.get('page_count', 0)} pages, {doc.get('chunk_count', 0)} passages ({note}).")
    for name in failed:
        reason = failed_details.get(name)
        st.error(f"Failed to process **{name}**. {reason}" if reason else f"Failed to process: {name}")


def _current_user_email() -> str | None:
    try:
        from utils.auth0_utils import get_user_email

        return get_user_email()
    except Exception:
        return None


def _index_uploaded_file(client, uploaded_file: Any) -> dict[str, Any]:
    """Index one uploaded PDF into Neon with a progress bar."""
    progress = st.progress(0.0, text=f"Processing **{uploaded_file.name}**…")
    try:
        return ingest_pdf(
            client,
            uploaded_file.getvalue(),
            uploaded_file.name,
            uploaded_by=_current_user_email(),
            on_progress=lambda message, fraction: progress.progress(
                fraction, text=f"**{uploaded_file.name}** — {message}"
            ),
        )
    finally:
        progress.empty()


def process_pending_pdf_uploads() -> None:
    """Index new PDFs from the uploader widget before sidebar navigation runs."""
    init_pdf_session_state()
    new_files = pending_uploader_files()
    if not new_files:
        return

    st.session_state.pdf_upload_in_progress = True
    added: list[str] = []
    failed: list[str] = []
    failed_details: dict[str, str] = {}
    try:
        try:
            client = get_cached_openai_client()
        except Exception as e:
            reason = format_llm_error(e)
            st.session_state.pdf_upload_last_failed = [uf.name for uf in new_files]
            st.session_state.pdf_upload_last_failed_details = {uf.name: reason for uf in new_files}
            return

        processed = st.session_state.setdefault("processed_pdf_names", set())
        by_upload = st.session_state.setdefault("spec_documents_by_upload", {})
        viewer_files = st.session_state.setdefault("pdf_viewer_files_by_upload", {})
        for uf in new_files:
            try:
                by_upload[uf.name] = _index_uploaded_file(client, uf)
            except Exception as e:
                failed.append(uf.name)
                failed_details[uf.name] = format_llm_error(e)
                continue
            viewer_files[uf.name] = uf.getvalue()
            if uf.name not in st.session_state.loaded_pdf_names:
                st.session_state.loaded_pdf_names.append(uf.name)
            processed.add(uf.name)
            added.append(uf.name)
        st.session_state.spec_documents_by_upload = by_upload
        st.session_state.pdf_viewer_files_by_upload = viewer_files
        if failed:
            existing = set(st.session_state.get("failed_pdf_uploads") or [])
            existing.update(failed)
            st.session_state.failed_pdf_uploads = sorted(existing)
            st.session_state.pop("unfinished_spec_documents", None)
        st.session_state.pdf_upload_last_added = added
        st.session_state.pdf_upload_last_failed = failed
        st.session_state.pdf_upload_last_failed_details = failed_details
        if added:
            st.session_state.pdf_upload_bump_uploader_key = True
    finally:
        st.session_state.pdf_upload_in_progress = False


def _unfinished_documents() -> list[dict[str, Any]]:
    if "unfinished_spec_documents" not in st.session_state:
        try:
            st.session_state.unfinished_spec_documents = list_unfinished_documents()
        except Exception:
            return []
    return st.session_state.unfinished_spec_documents


def _render_unfinished_cleanup() -> None:
    """Button to drop indexes that stopped before they were ready. Ready indexes are kept."""
    unfinished = _unfinished_documents()
    if not unfinished:
        return
    labels = ", ".join(
        f"{row['file_name']} ({row['chunk_count']} passages)" for row in unfinished
    )
    st.caption(f"Unfinished index, not used for answers: {labels}")
    if st.button("Delete unfinished indexes", key="delete_unfinished_indexes"):
        try:
            deleted = delete_unfinished_documents()
        except Exception as exc:
            st.error(format_llm_error(exc))
            return
        st.session_state.unfinished_spec_documents = []
        st.toast(f"Deleted {deleted} unfinished index(es).")
        st.rerun()


def render_pdf_uploader() -> None:
    """Upload specification PDFs; each is indexed once into the Neon vector store."""
    init_pdf_session_state()
    if st.session_state.pop("pdf_upload_bump_uploader_key", None):
        _bump_uploader_widget_key()
    names = st.session_state.get("loaded_pdf_names") or []
    uploaded_raw = st.file_uploader(
        "Upload specification PDF(s)",
        type=["pdf"],
        accept_multiple_files=True,
        key=_uploader_widget_key(),
        label_visibility="collapsed" if names else "visible",
        help=(
            "Upload construction specification PDFs (thousands of pages are supported). "
            "Each file is indexed once; re-uploading the same file reuses the stored index. "
            "Scanned PDFs without a text layer cannot be searched."
        ),
    ) or []
    raw_list = uploaded_raw if isinstance(uploaded_raw, list) else [uploaded_raw]
    uploaded = _dedupe_uploader_files(raw_list)
    if len(uploaded) < len(raw_list):
        _bump_uploader_widget_key()
        st.rerun()
    _sync_removed_uploaded_pdfs(uploaded)
    _show_upload_feedback()
    _render_unfinished_cleanup()

    if pdf_upload_in_progress():
        st.caption("Indexing PDF(s)…")
    elif names:
        st.caption("Attached: " + ", ".join(names))
    else:
        st.caption("Upload at least one PDF to enable chat and checklist auto-fill.")


def _attachment_names() -> str | None:
    names = st.session_state.get("loaded_pdf_names") or []
    return ", ".join(names) if names else None


def _selected_chat_model() -> str:
    model = st.session_state.get("selected_model", DEFAULT_CHAT_MODEL)
    return model if model in CHAT_MODELS else DEFAULT_CHAT_MODEL


def _extract_query_text(prompt_text: str) -> str:
    m = re.search(r"<query>(.*?)</query>", prompt_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return (prompt_text or "").strip()


def _trimmed_history(prior: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"role": turn["role"], "content": turn["content"][:HISTORY_MAX_CHARS]}
        for turn in prior[-HISTORY_MAX_MESSAGES:]
    ]


def _strip_citation_suffix(text: str) -> str:
    return text.split(CITATION_SUFFIX_MARKER, 1)[0].strip()


def _is_followup(text: str) -> bool:
    return FOLLOWUP_MARKER in text.lower() or len(text.split()) < FOLLOWUP_MAX_WORDS


def _retrieval_query(user_text: str, prior: list[dict[str, str]]) -> str:
    """Follow-ups (draft email, show sources…) search on the last substantive question."""
    base = _strip_citation_suffix(user_text)
    if not _is_followup(base):
        return base
    for turn in reversed(prior):
        if turn["role"] != "user":
            continue
        previous = _strip_citation_suffix(turn["content"])
        if not _is_followup(previous):
            return f"{previous}\n{base}" if len(base.split()) < FOLLOWUP_MAX_WORDS else previous
    return base


def generate_llm_response(
    prompt_text: str,
    include_history: bool = True,
) -> Generator[str, None, None]:
    """Stream assistant reply grounded in excerpts retrieved from the uploaded PDFs."""
    import utils.chat_threads

    user_text = _extract_query_text(prompt_text)
    documents = loaded_spec_documents()
    if not documents:
        yield "Upload at least one specification PDF before asking questions."
        return

    prior: list[dict[str, str]] = []
    if include_history:
        prior = utils.chat_threads.rebuild_chat_history_from_messages(
            st.session_state.get("messages", [])
        )
        if prior and prior[-1]["role"] == "user":
            prior = prior[:-1]

    try:
        client = get_cached_openai_client()
        excerpts = retrieve_for_chat(client, documents, _retrieval_query(user_text, prior))
        user_message = build_chat_user_message(user_text, format_excerpts(excerpts))
        input_messages = _trimmed_history(prior) + [{"role": "user", "content": user_message}]
        instructions = st.session_state.get("system_prompt", system_prompt)
        for partial in stream_chat_reply(client, _selected_chat_model(), instructions, input_messages):
            yield clean_llm_response(partial)
    except Exception as e:
        yield format_llm_error(e)


def append_user_message(user_prompt: str) -> bool:
    """Append a plain user message to session messages."""
    clean = (user_prompt or "").strip()
    if not clean:
        st.warning("Prompt is empty.")
        return False
    st.session_state.messages.append(("user", clean, None, None, _selected_chat_model()))
    return True


def get_attachment_names_for_log() -> str | None:
    return _attachment_names()
