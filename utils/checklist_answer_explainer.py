"""System prompt and parsing for checklist answer + LLM explanation (spec_checklist)."""

from __future__ import annotations

import json
import re
from typing import Any

from utils.checklist_schema import ChecklistField
from utils.results_form import NOT_SPECIFIED, parse_checklist_answer


def _strip_json_fence(text: str) -> str:
    """Remove optional markdown code fences around JSON."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _parse_json_payload(raw: str) -> dict[str, Any] | None:
    """Parse LLM JSON output; return None on failure."""
    text = _strip_json_fence(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _match_allowed_option(answer_text: str, field: ChecklistField) -> tuple[str, bool, str]:
    """Match answer string to allowed options (same rules as parse_checklist_answer)."""
    normalized = (answer_text or "").strip()
    if not normalized:
        return NOT_SPECIFIED, False, "empty answer in JSON"

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


def parse_answer_explanation(
    raw: str,
    field: ChecklistField,
) -> tuple[str, str, bool, str]:
    """
    Parse combined LLM JSON (answer + explanation).

    Returns (parsed_answer, explanation, parse_valid, parse_note).
    Falls back to plain-text answer parsing with an empty explanation on failure.
    """
    payload = _parse_json_payload(raw)
    if payload is None:
        parsed, parse_valid, parse_note = parse_checklist_answer(raw, field)
        note = "non-json response" if not parse_note else f"{parse_note}; non-json response"
        return parsed, "", parse_valid, note

    answer_raw = payload.get("answer", "")
    explanation_raw = payload.get("explanation", "")
    if not isinstance(answer_raw, str):
        answer_raw = str(answer_raw) if answer_raw is not None else ""
    if not isinstance(explanation_raw, str):
        explanation_raw = str(explanation_raw) if explanation_raw is not None else ""

    parsed, parse_valid, parse_note = _match_allowed_option(answer_raw, field)
    explanation = explanation_raw.strip()
    return parsed, explanation, parse_valid, parse_note
