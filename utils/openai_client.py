"""Shared helpers for the OpenAI SDK: chat streaming, structured output, embeddings."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Generator

import openai
from openai import OpenAI

from utils.llm_prompts import (
    CHAT_REASONING_EFFORT,
    CHECKLIST_BATCH_RESPONSE_SCHEMA,
    CHECKLIST_REASONING_EFFORT,
    OPENAI_EMBEDDING_DIMENSIONS,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_TITLE_MODEL,
    build_checklist_batch_user_message,
    build_pdf_checklist_batch_system_prompt,
)
from utils.streamlit_secrets import get_secret

EMBEDDING_BATCH_MAX_INPUTS = 64
EMBEDDING_BATCH_MAX_TOKENS = 40_000
EMBEDDING_TARGET_TOKENS_PER_MINUTE = 400_000
EMBEDDING_RATE_LIMIT_RETRIES = 8
_CHARS_PER_TOKEN = 4


def resolve_openai_api_key() -> str:
    """Return OPENAI_API_KEY from Streamlit secrets or `.streamlit/secrets.toml`."""
    try:
        import streamlit as st

        key = st.secrets.get("OPENAI_API_KEY")
        if key:
            return str(key).strip()
    except Exception:
        pass
    key = get_secret("OPENAI_API_KEY")
    if key:
        return str(key).strip()
    raise RuntimeError("OPENAI_API_KEY not configured. Add it to .streamlit/secrets.toml.")


def get_openai_client(api_key: str) -> OpenAI:
    if not api_key:
        raise ValueError("OPENAI_API_KEY is empty")
    return OpenAI(api_key=api_key, max_retries=5, timeout=300)


def get_cached_openai_client() -> OpenAI:
    """Cached OpenAI client for Streamlit (reads OPENAI_API_KEY from st.secrets)."""
    import streamlit as st

    @st.cache_resource
    def _client(api_key: str) -> OpenAI:
        return get_openai_client(api_key)

    return _client(resolve_openai_api_key())


def _is_reasoning_param_error(exc: Exception) -> bool:
    return isinstance(exc, openai.BadRequestError) and "reasoning" in str(exc).lower()


def _create_response(client: OpenAI, *, reasoning_effort: str | None, **kwargs: Any) -> Any:
    """Call Responses API; retry without reasoning when a model rejects that parameter."""
    if reasoning_effort:
        try:
            return client.responses.create(reasoning={"effort": reasoning_effort}, **kwargs)
        except openai.BadRequestError as exc:
            if not _is_reasoning_param_error(exc):
                raise
    return client.responses.create(**kwargs)


def stream_chat_reply(
    client: OpenAI,
    model: str,
    instructions: str,
    input_messages: list[dict[str, str]],
) -> Generator[str, None, None]:
    """Stream assistant text; yields the accumulated reply after each delta."""
    stream = _create_response(
        client,
        reasoning_effort=CHAT_REASONING_EFFORT,
        model=model,
        instructions=instructions,
        input=input_messages,
        stream=True,
    )
    full = ""
    for event in stream:
        event_type = getattr(event, "type", "")
        if event_type == "response.output_text.delta":
            full += event.delta or ""
            yield full
        elif event_type in ("response.failed", "error"):
            detail = getattr(getattr(event, "response", None), "error", None) or getattr(event, "message", "")
            raise RuntimeError(f"OpenAI response failed: {detail}")


def generate_json(
    client: OpenAI,
    model: str,
    instructions: str,
    user_text: str,
    schema: dict[str, Any],
    *,
    schema_name: str,
    reasoning_effort: str | None = CHECKLIST_REASONING_EFFORT,
) -> str:
    """One-shot structured output; returns the JSON text."""
    response = _create_response(
        client,
        reasoning_effort=reasoning_effort,
        model=model,
        instructions=instructions,
        input=[{"role": "user", "content": user_text}],
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "schema": schema,
                "strict": True,
            }
        },
    )
    return (response.output_text or "").strip()


def generate_short_text(client: OpenAI, prompt: str, *, model: str = OPENAI_TITLE_MODEL) -> str:
    """Small one-shot completion (chat titles)."""
    response = client.responses.create(model=model, input=prompt)
    return (response.output_text or "").strip()


def generate_checklist_batch(
    client: OpenAI,
    model: str,
    fields_payload: list[dict[str, Any]],
    excerpts_block: str,
    *,
    system_instruction: str | None = None,
) -> str:
    """Up to 10 checklist fields in one call; returns JSON text with a "fields" array."""
    return generate_json(
        client,
        model,
        system_instruction or build_pdf_checklist_batch_system_prompt(),
        build_checklist_batch_user_message(fields_payload, excerpts_block),
        CHECKLIST_BATCH_RESPONSE_SCHEMA,
        schema_name="checklist_batch",
    )


def parse_checklist_batch_response(raw: str) -> list[dict[str, Any]]:
    """Parse batch JSON from model output."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    data = json.loads(text)
    if isinstance(data, dict) and "fields" in data:
        data = data["fields"]
    if not isinstance(data, list):
        raise ValueError("Batch response is not a JSON array")
    return data


def _embedding_batches(texts: list[str]) -> list[tuple[int, list[str]]]:
    """Split texts into (start_index, batch) pairs within per-request input/token limits."""
    batches: list[tuple[int, list[str]]] = []
    start = 0
    current: list[str] = []
    current_tokens = 0
    for i, text in enumerate(texts):
        tokens = len(text) // _CHARS_PER_TOKEN + 1
        if current and (
            len(current) >= EMBEDDING_BATCH_MAX_INPUTS
            or current_tokens + tokens > EMBEDDING_BATCH_MAX_TOKENS
        ):
            batches.append((start, current))
            start, current, current_tokens = i, [], 0
        current.append(text)
        current_tokens += tokens
    if current:
        batches.append((start, current))
    return batches


def _is_quota_error(exc: openai.RateLimitError) -> bool:
    text = str(exc).lower()
    return "insufficient_quota" in text or "billing" in text


def _header_seconds(headers: Any, name: str) -> float | None:
    value = None
    for key in headers:
        if str(key).lower() == name:
            value = headers[key]
            break
    if value is None:
        return None
    text = str(value).strip().lower()
    try:
        if text.endswith("ms"):
            return float(text[:-2]) / 1000.0
        if text.endswith("s"):
            return float(text[:-1])
        return float(text)
    except ValueError:
        return None


def _retry_after_seconds(exc: openai.RateLimitError, attempt: int) -> float:
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    waits = [
        _header_seconds(headers, "retry-after"),
        _header_seconds(headers, "x-ratelimit-reset-requests"),
        _header_seconds(headers, "x-ratelimit-reset-tokens"),
    ]
    hinted = max((wait for wait in waits if wait is not None), default=0.0)
    fallback = min(60.0, 2.0 * (2**attempt))
    return min(90.0, max(hinted, fallback, 2.0))


def _embed_batch(
    client: OpenAI,
    batch: list[str],
    *,
    on_wait: Callable[[str], None] | None = None,
    on_limited: Callable[[], None] | None = None,
) -> list[list[float]]:
    """Embed one batch. On a rate limit, wait for the time OpenAI asks and retry."""
    for attempt in range(EMBEDDING_RATE_LIMIT_RETRIES + 1):
        try:
            response = client.with_options(max_retries=0).embeddings.create(
                model=OPENAI_EMBEDDING_MODEL,
                input=batch,
                dimensions=OPENAI_EMBEDDING_DIMENSIONS,
            )
            return [item.embedding for item in sorted(response.data, key=lambda d: d.index)]
        except openai.RateLimitError as exc:
            if _is_quota_error(exc) or attempt == EMBEDDING_RATE_LIMIT_RETRIES:
                raise
            if on_limited:
                on_limited()
            delay = _retry_after_seconds(exc, attempt)
            if on_wait:
                on_wait(f"OpenAI rate limit — waiting {int(delay)}s, then continuing…")
            time.sleep(delay)
    raise RuntimeError("OpenAI embedding retries were exhausted.")


def _pace_embedding_request(tokens: int, tokens_per_minute: int, started: float) -> None:
    """Keep a single request inside the token budget so a large PDF does not burst the limit."""
    budget_seconds = tokens / tokens_per_minute * 60.0
    remaining = budget_seconds - (time.monotonic() - started)
    if remaining > 0:
        time.sleep(remaining)


def iter_embedding_batches(
    client: OpenAI,
    texts: list[str],
    *,
    on_wait: Callable[[str], None] | None = None,
) -> Generator[tuple[int, list[list[float]]], None, None]:
    """Embed texts one batch at a time; yield (start_index, vectors) after each batch."""
    cleaned = [(t or " ").replace("\x00", " ") for t in texts]
    pace = {"tokens_per_minute": EMBEDDING_TARGET_TOKENS_PER_MINUTE}

    def slow_down() -> None:
        pace["tokens_per_minute"] = max(50_000, pace["tokens_per_minute"] // 2)

    for start, batch in _embedding_batches(cleaned):
        started = time.monotonic()
        vectors = _embed_batch(client, batch, on_wait=on_wait, on_limited=slow_down)
        if len(vectors) != len(batch):
            raise RuntimeError("OpenAI returned fewer embeddings than requested.")
        yield start, vectors
        tokens = sum(len(text) // _CHARS_PER_TOKEN + 1 for text in batch)
        _pace_embedding_request(tokens, pace["tokens_per_minute"], started)


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    """Embed a small list of texts; preserves input order."""
    vectors: list[list[float]] = [[] for _ in texts]
    for start, batch_vectors in iter_embedding_batches(client, texts):
        vectors[start : start + len(batch_vectors)] = batch_vectors
    return vectors
