"""Line-buffered terminal logging for QA checklist scripts."""

from __future__ import annotations

import os
import sys
from datetime import datetime


def verbose_enabled() -> bool:
    raw = (os.environ.get("QA_VERBOSE") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _safe_print(line: str) -> None:
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(enc, errors="replace").decode(enc), flush=True)


def verbose_log(tag: str, msg: str) -> None:
    if not verbose_enabled():
        return
    ts = datetime.now().strftime("%H:%M:%S")
    safe = msg.replace("✓", "OK").replace("✗", "FAIL").replace("⚠", "WARN")
    _safe_print(f"[{ts}] [{tag}] {safe}")


def verbose_block(tag: str, title: str, text: str, *, indent: str = "    ") -> None:
    if not verbose_enabled():
        return
    body = (text or "").strip() or "(empty)"
    verbose_log(tag, f"--- {title} ---")
    for line in body.splitlines():
        _safe_print(f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {indent}{line}")
    verbose_log(tag, f"--- end {title} ---")


def configure_unbuffered_stdout() -> None:
    """Force immediate terminal output when stdout is piped (pipeline subprocess)."""
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        pass
