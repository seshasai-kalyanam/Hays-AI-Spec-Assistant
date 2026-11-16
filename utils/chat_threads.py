"""User-visible chat history helpers for the Streamlit app."""

from __future__ import annotations

import math
import re
from typing import Any

import streamlit as st

from utils.openai_client import generate_short_text, get_openai_client, resolve_openai_api_key
from utils.neon_utils import (
    archive_chat_thread,
    count_chat_threads,
    create_chat_thread,
    list_chat_threads,
    read_chat_thread,
    rename_chat_thread,
    update_chat_thread,
)
from utils.text_cleanup import clean_llm_response


CHAT_HISTORY_PAGE_SIZE = 25
CHAT_HISTORY_TITLE_CHARS = 38
TITLE_STATUS_PENDING = "pending"
TITLE_STATUS_GENERATED = "generated"
TITLE_STATUS_FAILED = "failed"
THREAD_SESSION_DEFAULTS: dict[str, Any] = {
    "active_thread_id": None,
    "active_thread_title": "New chat",
    "active_thread_title_status": TITLE_STATUS_PENDING,
    "thread_list": [],
    "thread_count": 0,
    "thread_list_loaded": False,
    "pending_thread_id": None,
    "pending_new_thread": False,
    "pending_delete_thread_id": None,
    "confirm_delete_thread_id": None,
    "renaming_thread_id": None,
    "pending_rename_thread_id": None,
    "pending_rename_title": None,
    "chat_history_page": 0,
    "last_persisted_message_count": 0,
    "title_generation_in_progress": False,
}


def init_thread_session_defaults() -> None:
    for key, value in THREAD_SESSION_DEFAULTS.items():
        st.session_state.setdefault(key, value)


def apply_initial_blank_state() -> None:
    """Start the app with a blank chat unless this run is handling history input."""
    if st.session_state.get("chat_history_initial_blank_applied"):
        return

    has_pending_history_action = any(
        st.session_state.get(key)
        for key in (
            "pending_thread_id",
            "pending_new_thread",
            "pending_delete_thread_id",
            "pending_rename_thread_id",
        )
    )
    st.session_state["chat_history_initial_blank_applied"] = True
    if not has_pending_history_action:
        hydrate_empty_thread(None)


def history_storage_available() -> bool:
    return bool(st.session_state.get("_chat_threads_table_ready"))


def _normalize_title(title: str | None) -> str:
    cleaned = (title or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "New chat"


def _extract_query_text(prompt: str) -> str:
    match = re.search(r"<query>(.*?)</query>", prompt or "", flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    text = re.sub(r"<context.*?</context>", "", prompt or "", flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fallback_title(first_user_prompt: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9/&.-]*", _extract_query_text(first_user_prompt))
    if not words:
        return "New chat"
    return _normalize_title(" ".join(words[:8]))


def _compact_title(title: str) -> str:
    title = _normalize_title(title)
    if len(title) <= CHAT_HISTORY_TITLE_CHARS:
        return title
    return title[: CHAT_HISTORY_TITLE_CHARS - 3].rstrip() + "..."


def _json_safe_messages(messages: list[Any]) -> list[list[Any]]:
    return [list(message) for message in messages or []]


def _messages_from_json(messages: list[Any]) -> list[tuple[Any, ...]]:
    return [tuple(message) for message in messages or []]


def _json_safe_log_entry_ids(log_entry_ids: dict[Any, Any]) -> dict[str, int]:
    safe: dict[str, int] = {}
    for key, value in (log_entry_ids or {}).items():
        try:
            safe[str(int(key))] = int(value)
        except (TypeError, ValueError):
            continue
    return safe


def _log_entry_ids_from_json(log_entry_ids: dict[Any, Any]) -> dict[int, int]:
    normalized: dict[int, int] = {}
    for key, value in (log_entry_ids or {}).items():
        try:
            normalized[int(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return normalized


def rebuild_chat_history_from_messages(messages: list[Any]) -> list[dict[str, str]]:
    """Slim {role, content} pairs for the OpenRouter request; matches past chat_history appends.

    For user turns, content is the <query>…</query> text when present, else the full first segment.
    """
    out: list[dict[str, str]] = []
    for m in messages or []:
        if not m:
            continue
        role = m[0]
        if role == "user" and len(m) > 1:
            prompt_text = m[1]
            qm = re.search(
                r"<query>(.*?)</query>", str(prompt_text), flags=re.DOTALL
            )
            content = qm.group(1).strip() if qm else str(prompt_text)
            out.append({"role": "user", "content": content})
        elif role == "assistant" and len(m) > 1:
            out.append({"role": "assistant", "content": str(m[1])})
    return out


def serialize_session_thread_state() -> dict[str, Any]:
    return {
        "recents": _json_safe_messages(st.session_state.get("messages", [])),
        "log_entry_ids": _json_safe_log_entry_ids(
            st.session_state.get("log_entry_ids", {})
        ),
    }


def clear_local_chat_state(default_kb_on: bool = True) -> None:
    del default_kb_on  # retained for call-site compatibility
    st.session_state["messages"] = []
    st.session_state.pop("chat_history", None)
    st.session_state["collected_answers"] = []
    st.session_state["log_entry_ids"] = {}
    st.session_state["feedback_states"] = {}
    st.session_state.pop("last_log_id", None)
    st.session_state.pop("previous_context", None)


def hydrate_thread(thread: dict[str, Any]) -> None:
    clear_local_chat_state(default_kb_on=False)
    st.session_state["active_thread_id"] = str(thread["id"])
    st.session_state["active_thread_title"] = thread.get("title") or "New chat"
    st.session_state["active_thread_title_status"] = (
        thread.get("title_status") or TITLE_STATUS_PENDING
    )
    st.session_state["messages"] = _messages_from_json(
        thread.get("recents")
        or thread.get("messages")
        or []
    )
    st.session_state["collected_answers"] = []
    st.session_state["log_entry_ids"] = _log_entry_ids_from_json(
        thread.get("log_entry_ids") or {}
    )
    st.session_state["feedback_states"] = {}
    st.session_state["last_persisted_message_count"] = len(
        st.session_state["messages"]
    )


def hydrate_empty_thread(thread: dict[str, Any] | None = None) -> None:
    clear_local_chat_state(default_kb_on=True)
    st.session_state["active_thread_id"] = str(thread["id"]) if thread else None
    st.session_state["active_thread_title"] = (
        (thread or {}).get("title") or "New chat"
    )
    st.session_state["active_thread_title_status"] = (
        (thread or {}).get("title_status") or TITLE_STATUS_PENDING
    )
    st.session_state["last_persisted_message_count"] = 0


def _first_user_and_assistant() -> tuple[str | None, str | None]:
    first_user: str | None = None
    first_assistant: str | None = None
    for message in st.session_state.get("messages", []):
        if not message:
            continue
        if message[0] == "user" and first_user is None:
            first_user = message[1]
        elif message[0] == "assistant" and first_user is not None:
            first_assistant = message[1]
            break
    return first_user, first_assistant


def generate_chat_title(
    first_user_prompt: str,
    first_assistant_response: str,
) -> tuple[str, str]:
    fallback = _fallback_title(first_user_prompt)
    try:
        try:
            api_key = resolve_openai_api_key()
        except RuntimeError:
            return fallback, TITLE_STATUS_FAILED

        prompt = (
            "Create a concise title, 3 to 8 words, for this chat.\n"
            "Return only the title. No quotes, no punctuation unless necessary.\n\n"
            f"User: {_extract_query_text(first_user_prompt)}\n"
            f"Assistant: {first_assistant_response[:2000]}"
        )
        title = generate_short_text(get_openai_client(api_key), prompt)
        title = clean_llm_response(title).strip().strip("\"'")
        title = title.splitlines()[0] if title else ""
        return _normalize_title(title or fallback), TITLE_STATUS_GENERATED
    except Exception:
        return fallback, TITLE_STATUS_FAILED


def refresh_thread_list(user_email: str) -> None:
    if not history_storage_available():
        st.session_state["thread_list"] = []
        st.session_state["thread_count"] = 0
        st.session_state["thread_list_loaded"] = False
        return

    page = max(0, int(st.session_state.get("chat_history_page", 0)))
    count = count_chat_threads(user_email)
    max_page = max(0, math.ceil(count / CHAT_HISTORY_PAGE_SIZE) - 1)
    page = min(page, max_page)
    st.session_state["chat_history_page"] = page
    st.session_state["thread_count"] = count
    st.session_state["thread_list"] = list_chat_threads(
        user_email,
        limit=CHAT_HISTORY_PAGE_SIZE,
        offset=page * CHAT_HISTORY_PAGE_SIZE,
    )
    st.session_state["thread_list_loaded"] = True


def persist_active_thread(
    user_email: str,
    force: bool = False,
) -> dict[str, Any] | None:
    if not history_storage_available():
        return None
    thread_id = st.session_state.get("active_thread_id")

    message_count = len(st.session_state.get("messages", []))
    if not thread_id:
        if message_count == 0:
            return None
        thread = create_chat_thread(user_email=user_email)
        if not thread:
            st.session_state["_chat_threads_table_ready"] = False
            return None
        st.session_state["active_thread_id"] = str(thread["id"])
        st.session_state["active_thread_title"] = thread.get("title") or "New chat"
        st.session_state["active_thread_title_status"] = (
            thread.get("title_status") or TITLE_STATUS_PENDING
        )
        thread_id = st.session_state["active_thread_id"]

    is_dirty = bool(st.session_state.get("thread_dirty"))
    if (
        not force
        and not is_dirty
        and message_count == st.session_state.get("last_persisted_message_count")
    ):
        return None

    title = None
    title_status = None
    current_status = st.session_state.get(
        "active_thread_title_status", TITLE_STATUS_PENDING
    )
    if current_status == TITLE_STATUS_PENDING:
        first_user, first_assistant = _first_user_and_assistant()
        if first_user and first_assistant:
            title, title_status = generate_chat_title(first_user, first_assistant)

    state = serialize_session_thread_state()
    updated = update_chat_thread(
        thread_id,
        user_email,
        recents=state["recents"],
        log_entry_ids=state["log_entry_ids"],
        title=title,
        title_status=title_status,
    )
    if updated:
        st.session_state["last_persisted_message_count"] = message_count
        st.session_state.pop("thread_dirty", None)
        st.session_state["active_thread_title"] = updated.get("title") or "New chat"
        st.session_state["active_thread_title_status"] = (
            updated.get("title_status") or TITLE_STATUS_PENDING
        )
    return updated


def ensure_active_thread(
    user_email: str,
    model: str | None = None,
) -> None:
    if not history_storage_available() or not user_email:
        st.session_state.setdefault("active_thread_id", None)
        st.session_state.setdefault("active_thread_title", "New chat")
        st.session_state.setdefault("active_thread_title_status", TITLE_STATUS_PENDING)
        return

    active_id = st.session_state.get("active_thread_id")
    if active_id:
        existing = read_chat_thread(active_id, user_email)
        if existing:
            return
        hydrate_empty_thread(None)
        return

    st.session_state.setdefault("messages", [])
    st.session_state.pop("chat_history", None)
    st.session_state.setdefault("collected_answers", [])
    st.session_state.setdefault("log_entry_ids", {})
    st.session_state.setdefault("feedback_states", {})
    st.session_state["active_thread_title"] = "New chat"
    st.session_state["active_thread_title_status"] = TITLE_STATUS_PENDING


def _select_thread(
    thread_id: str,
    user_email: str,
    model: str | None,
) -> None:
    if thread_id == st.session_state.get("active_thread_id"):
        return
    persist_active_thread(user_email, force=True)
    thread = read_chat_thread(thread_id, user_email)
    if thread:
        hydrate_thread(thread)
    else:
        ensure_active_thread(user_email, model)


def _start_new_thread(
    user_email: str,
    model: str | None,
) -> None:
    persist_active_thread(user_email, force=True)
    hydrate_empty_thread(None)
    st.session_state["chat_history_page"] = 0


def consume_pending_actions(
    user_email: str,
    model: str | None = None,
) -> None:
    init_thread_session_defaults()
    if not history_storage_available() or not user_email:
        return

    rename_id = st.session_state.pop("pending_rename_thread_id", None)
    if rename_id:
        title = _normalize_title(st.session_state.pop("pending_rename_title", None))
        renamed = rename_chat_thread(rename_id, user_email, title)
        if renamed and str(renamed["id"]) == st.session_state.get("active_thread_id"):
            st.session_state["active_thread_title"] = renamed.get("title") or title
            st.session_state["active_thread_title_status"] = "manual"
        st.session_state["renaming_thread_id"] = None

    delete_id = st.session_state.pop("pending_delete_thread_id", None)
    if delete_id:
        archive_chat_thread(delete_id, user_email)
        if delete_id == st.session_state.get("active_thread_id"):
            st.session_state["active_thread_id"] = None
            ensure_active_thread(user_email, model)
        st.session_state["confirm_delete_thread_id"] = None

    if st.session_state.pop("pending_new_thread", False):
        _start_new_thread(user_email, model)

    pending_thread_id = st.session_state.pop("pending_thread_id", None)
    if pending_thread_id:
        _select_thread(pending_thread_id, user_email, model)


def queue_select_thread(thread_id: str) -> None:
    st.session_state["pending_thread_id"] = thread_id


def queue_new_thread() -> None:
    st.session_state["pending_new_thread"] = True


def request_delete_thread(thread_id: str) -> None:
    st.session_state["confirm_delete_thread_id"] = thread_id


def confirm_delete_thread(thread_id: str) -> None:
    st.session_state["pending_delete_thread_id"] = thread_id
    st.session_state["confirm_delete_thread_id"] = None


def cancel_delete_thread() -> None:
    st.session_state["confirm_delete_thread_id"] = None


def start_rename_thread(thread_id: str, current_title: str) -> None:
    st.session_state["renaming_thread_id"] = thread_id
    st.session_state[f"rename_title_{thread_id}"] = current_title


def queue_rename_thread(thread_id: str) -> None:
    st.session_state["pending_rename_thread_id"] = thread_id
    st.session_state["pending_rename_title"] = st.session_state.get(
        f"rename_title_{thread_id}",
        "",
    )


def cancel_rename_thread() -> None:
    st.session_state["renaming_thread_id"] = None


def previous_page() -> None:
    st.session_state["chat_history_page"] = max(
        0,
        int(st.session_state.get("chat_history_page", 0)) - 1,
    )


def next_page() -> None:
    st.session_state["chat_history_page"] = int(
        st.session_state.get("chat_history_page", 0)
    ) + 1


def render_sidebar_history(disabled: bool = False) -> None:
    """Compact thread list; caller wraps in sidebar (e.g. a Recents expander)."""
    if not history_storage_available():
        st.caption("Saved history is unavailable. Current-session chat still works.")
        return

    threads = st.session_state.get("thread_list", [])
    total = int(st.session_state.get("thread_count", 0))
    if not threads:
        st.caption("No saved chats yet.")
        return

    active_id = st.session_state.get("active_thread_id")
    seen_compact_titles: set[str] = set()
    for thread in threads:
        thread_id = str(thread["id"])
        title = thread.get("title") or "New chat"
        base_compact = _compact_title(title)
        if st.session_state.get("renaming_thread_id") == thread_id:
            st.text_input(
                "Chat title",
                key=f"rename_title_{thread_id}",
                label_visibility="collapsed",
            )
            save_col, cancel_col = st.columns(2, gap="small")
            with save_col:
                st.button(
                    "✓",
                    key=f"save_rename_{thread_id}",
                    on_click=queue_rename_thread,
                    args=(thread_id,),
                    disabled=disabled,
                    help="Save title",
                    width="stretch",
                )
            with cancel_col:
                st.button(
                    "×",
                    key=f"cancel_rename_{thread_id}",
                    on_click=cancel_rename_thread,
                    disabled=disabled,
                    help="Cancel rename",
                    width="stretch",
                )
            continue

        if base_compact in seen_compact_titles:
            list_label = f"{base_compact} · {str(thread_id)[:8]}"
        else:
            list_label = base_compact
        seen_compact_titles.add(base_compact)

        title_col, edit_col, delete_col = st.columns([8, 1, 1], gap="small")
        label = f"● {list_label}" if thread_id == active_id else list_label
        with title_col:
            st.button(
                label,
                key=f"select_thread_{thread_id}",
                on_click=queue_select_thread,
                args=(thread_id,),
                disabled=disabled,
                help=title,
                type="tertiary",
                width="content",
            )
        with edit_col:
            st.button(
                "",
                key=f"edit_thread_{thread_id}",
                on_click=start_rename_thread,
                args=(thread_id, title),
                disabled=disabled,
                help="Rename chat",
                icon=":material/edit:",
            )
        with delete_col:
            st.button(
                "",
                key=f"delete_thread_{thread_id}",
                on_click=request_delete_thread,
                args=(thread_id,),
                disabled=disabled,
                help="Delete chat",
                icon=":material/delete:",
            )

        if st.session_state.get("confirm_delete_thread_id") == thread_id:
            st.caption(f"Delete '{list_label}'?")
            confirm_col, cancel_col = st.columns(2, gap="small")
            with confirm_col:
                st.button(
                    "✓",
                    key=f"confirm_delete_{thread_id}",
                    on_click=confirm_delete_thread,
                    args=(thread_id,),
                    disabled=disabled,
                    help="Confirm delete",
                    width="stretch",
                )
            with cancel_col:
                st.button(
                    "×",
                    key=f"cancel_delete_{thread_id}",
                    on_click=cancel_delete_thread,
                    disabled=disabled,
                    help="Cancel delete",
                    width="stretch",
                )

    if total > CHAT_HISTORY_PAGE_SIZE:
        page = int(st.session_state.get("chat_history_page", 0))
        total_pages = max(1, math.ceil(total / CHAT_HISTORY_PAGE_SIZE))
        prev_col, page_col, next_col = st.columns([1, 2, 1], gap="small")
        with prev_col:
            st.button(
                "‹",
                key="chat_history_prev",
                on_click=previous_page,
                disabled=disabled or page <= 0,
                help="Previous page",
            )
        with page_col:
            st.caption(f"{page + 1}/{total_pages}")
        with next_col:
            st.button(
                "›",
                key="chat_history_next",
                on_click=next_page,
                disabled=disabled or page >= total_pages - 1,
                help="Next page",
            )
