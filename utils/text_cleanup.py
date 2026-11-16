import re
from typing import Dict, List


_TAG_REPLACEMENTS: Dict[str, str] = {
    "<verdict>": "**Verdict.** ",
    "</verdict>": "\n\n",
    "<detail_blocks>": "",
    "</detail_blocks>": "",
    "<gaps>": "**Gaps & Follow-Up**\n",
    "</gaps>": "\n\n",
    "<terminology_notes>": "**Terminology Notes**\n",
    "</terminology_notes>": "\n\n",
    "<verification_reminder>": "**Verification & Coordination**\n",
    "</verification_reminder>": "\n\n",
    "<supporting_details>": "**Supporting Details**\n",
    "</supporting_details>": "\n\n",
}

_UNICODE_REPLACEMENTS: Dict[str, str] = {
    "\u00b0": " deg",  # degree symbol -> ASCII
    "\u00d7": " x ",  # multiplication symbol -> ASCII
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u201c": '"',
    "\u201d": '"',
    "\u2264": "<=",
    "\u2265": ">=",
}

_TAG_PATTERN = re.compile(r"</?([a-zA-Z_]+)>")

# Header for the mandatory citations block (markdown bold).
_SOURCES_HEADER_RE = re.compile(r"(\*\*Sources\*\*)", re.IGNORECASE)
# "Source 1: ..." line (optional markdown bullet) or jammed sources on one line.
_SOURCE_LINE_START_RE = re.compile(
    r"^\s*(?:[-*•]\s+)?Source\s+\d+\s*:",
    re.IGNORECASE,
)


def _normalize_sources_block(text: str) -> str:
    """Ensure each ``Source N:`` citation is on its own Markdown paragraph.

    Single newlines inside a paragraph collapse to spaces in GFM / Streamlit
    markdown, so citations jammed on one line (or separated by only ``\\n``)
    must become blank-line-separated paragraphs. URLs and ``#page=`` fragments
    are never rewritten — only whitespace / newlines between labels.
    """
    if not text or not text.strip():
        return text
    m = _SOURCES_HEADER_RE.search(text)
    if not m:
        return text

    head = text[: m.end()]
    tail = text[m.end() :]

    # Split "Source 1: url Source 2: url" onto separate lines (preserve URL text).
    tail = re.sub(
        r"(\S)\s+(Source\s+\d+\s*:)",
        r"\1\n\2",
        tail,
        flags=re.IGNORECASE,
    )

    raw_lines = tail.split("\n")
    idx = 0
    while idx < len(raw_lines) and not raw_lines[idx].strip():
        idx += 1

    source_block: List[str] = []
    j = idx
    while j < len(raw_lines):
        line = raw_lines[j]
        if _SOURCE_LINE_START_RE.match(line):
            source_block.append(line.strip())
            j += 1
            continue
        if not line.strip():
            j += 1
            if source_block:
                break
            continue
        break

    if not source_block:
        return text

    rest = "\n".join(raw_lines[j:]).lstrip("\n")
    formatted = "\n\n".join(source_block)
    new_tail = "\n\n" + formatted + ("\n\n" + rest if rest else "")
    return head + new_tail


def remove_context_tags(text: str) -> str:
    """Strip ``<context>...</context>`` blocks used to inject KB snippets."""
    return re.sub(r"<context>.*?</context>", "", text or "", flags=re.DOTALL)


def strip_html_tags(text: str) -> str:
    """Remove any remaining angle-bracket tags and trim surrounding whitespace."""
    return re.sub(r"<.*?>", "", text or "").strip()


def clean_llm_response(text: str) -> str:
    """
    Normalize legacy tag wrappers and replace non-ASCII punctuation
    to keep downstream markdown clean.
    """
    if not text:
        return text

    cleaned = text.replace("\r\n", "\n")

    # Apply tag substitutions
    for tag, replacement in _TAG_REPLACEMENTS.items():
        cleaned = cleaned.replace(tag, replacement)

    # Strip any leftover angle-bracket tags
    cleaned = _TAG_PATTERN.sub("", cleaned)

    # Normalize problematic unicode to ASCII-safe equivalents
    for symbol, replacement in _UNICODE_REPLACEMENTS.items():
        cleaned = cleaned.replace(symbol, replacement)

    # One citation per line in **Sources** (Streamlit markdown needs paragraph breaks).
    cleaned = _normalize_sources_block(cleaned)

    # Collapse excess whitespace
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()

