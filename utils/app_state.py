"""Centralized session-state defaults """

from __future__ import annotations

import logging
from typing import Any, Dict

import streamlit as st

import utils.chat_threads
from utils.neon_utils import create_chat_table
from utils.neon_utils import create_user_chat_threads_table

logger = logging.getLogger("aifab.app_state")


SESSION_DEFAULTS: Dict[str, Any] = {
    "log_entry_ids": {},
    "feedback_states": {},
}


def init_session_defaults() -> None:
    """Populate st.session_state with defaults without overwriting existing values."""
    for key, value in SESSION_DEFAULTS.items():
        st.session_state.setdefault(key, value)


def _ensure_chat_table() -> None:
    """Ensure Neon chat-log table exists (best-effort; later reruns retry if needed)."""
    if st.session_state.get("_chat_table_ready"):
        return
    try:
        if create_chat_table():
            st.session_state["_chat_table_ready"] = True
    except Exception:
        logger.exception("create_chat_table failed")


def _ensure_user_chat_threads_table() -> None:
    """Ensure Neon chat-thread table exists (best-effort; later reruns retry if needed)."""
    if st.session_state.get("_chat_threads_table_ready"):
        return
    try:
        if create_user_chat_threads_table():
            st.session_state["_chat_threads_table_ready"] = True
            return
    except Exception:
        logger.exception("create_user_chat_threads_table failed")

    st.session_state["_chat_threads_table_ready"] = False


_SILENT_LEVEL_NAME = "SILENT"
_SILENT_NOISY_LOGGERS = (
    "streamlit",
    "stripe",
    "httpx",
    "httpcore",
    "urllib3",
    "openai",
    "asyncio",
)


def _resolve_log_level_name() -> str:
    """Return the configured log level name from ``st.secrets['AIFAB_LOG_LEVEL']``.

    Defaults to ``SILENT``, which silences all terminal output (including
    third-party chatter) unless the operator opts in via secrets.toml.
    """
    try:
        secret_level = st.secrets.get("AIFAB_LOG_LEVEL", None)
    except Exception:
        secret_level = None
    level = secret_level if secret_level else _SILENT_LEVEL_NAME
    return str(level).strip().upper() or _SILENT_LEVEL_NAME


def _apply_silent_logging() -> None:
    """Suppress all log output: no handlers emit, and noisy third-party loggers are muted."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(logging.NullHandler())
    # CRITICAL + 1 ensures even logger.critical(...) calls are discarded.
    root.setLevel(logging.CRITICAL + 1)

    for name in _SILENT_NOISY_LOGGERS:
        noisy = logging.getLogger(name)
        noisy.setLevel(logging.CRITICAL + 1)
        noisy.propagate = False


def _apply_standard_logging(level_name: str) -> None:
    """Configure a single structured stderr handler at the requested level."""
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _configure_logging() -> None:
    """Configure structured logging once per process.

    Defaults to SILENT (no terminal output) unless ``AIFAB_LOG_LEVEL`` is set
    in ``.streamlit/secrets.toml`` or the environment to ``DEBUG``, ``INFO``,
    ``WARNING``, ``ERROR``, or ``CRITICAL``.
    """
    if getattr(_configure_logging, "_done", False):
        return

    level_name = _resolve_log_level_name()
    if level_name == _SILENT_LEVEL_NAME:
        _apply_silent_logging()
    else:
        _apply_standard_logging(level_name)

    _configure_logging._done = True  # type: ignore[attr-defined]


def bootstrap() -> None:
    """Run per-rerun setup for this app."""
    _configure_logging()
    init_session_defaults()
    utils.chat_threads.init_thread_session_defaults()
    _ensure_chat_table()
    _ensure_user_chat_threads_table()
