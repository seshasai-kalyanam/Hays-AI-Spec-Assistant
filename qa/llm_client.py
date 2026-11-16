"""Gemini client helpers to mirror the app.py behaviour for QA scripts."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Optional

import streamlit as st  # type: ignore
from google import genai
from google.genai import errors, types

from utils.gemini_client import generation_config_from_dict, get_genai_client, history_dicts_to_contents

from utils.gemini_prompts import GEMINI_DEFAULT_MODEL, GEMINI_GENERATION_CONFIG

# Keep model and generation config aligned with app.py
MODEL_NAME = GEMINI_DEFAULT_MODEL
GENERATION_CONFIG = dict(GEMINI_GENERATION_CONFIG)

_genai_client: Optional[genai.Client] = None


def _resolve_api_key() -> Optional[str]:
    """Resolve GEMINI_API_KEY from streamlit secrets."""
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


def configure_client() -> None:
    """Configure the Gemini client, raising a clear error if the key is missing."""
    global _genai_client
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Export it or add it to .streamlit/secrets.toml."
        )
    _genai_client = get_genai_client(api_key)


def _get_genai_client() -> genai.Client:
    """Get or create the google-genai client."""
    global _genai_client
    if _genai_client is None:
        configure_client()
    assert _genai_client is not None
    return _genai_client


def get_gemini_client() -> genai.Client:
    """Return the shared client (calls configure_client if needed)."""
    return _get_genai_client()


def upload_document(path: str | Path, mime_type: Optional[str] = None):
    """Upload a document to Gemini and wait until it is ACTIVE."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Context file not found: {path}")

    client = _get_genai_client()
    upload_cfg = types.UploadFileConfig(mime_type=mime_type) if mime_type else None
    file = client.files.upload(file=path, config=upload_cfg)
    return wait_for_file_active(file)


def wait_for_file_active(file, timeout_seconds: int = 180, poll_seconds: int = 5):
    """Poll until a Gemini file is ACTIVE or fail fast on errors/timeouts."""
    client = _get_genai_client()
    start = time.time()
    while True:
        refreshed = client.files.get(name=file.name)
        state = refreshed.state.name

        if state == "ACTIVE":
            return refreshed
        if state == "FAILED":
            raise RuntimeError(f"Gemini file failed to process: {refreshed.state}")
        if time.time() - start > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for Gemini file {file.name} to be ACTIVE")

        time.sleep(poll_seconds)


def start_chat_with_context(system_prompt: str, files: Iterable) -> genai.chats.Chat:
    """Start a fresh chat session seeded with the uploaded files, matching app.py's create_new_chat_session pattern."""
    client = _get_genai_client()
    history = [
        {
            "role": "user",
            "parts": [
                *files,
                "Analyze all these files and keep their content in context for answering questions.",
            ],
        },
        {
            "role": "model",
            "parts": ["I have analyzed the files. I am ready to answer your questions based on their content."],
        },
    ]
    return client.chats.create(
        model=MODEL_NAME,
        config=generation_config_from_dict(GENERATION_CONFIG, system_instruction=system_prompt),
        history=history_dicts_to_contents(history),
    )


def answer_question(
    question: str,
    system_prompt: str,
    files: Iterable,
) -> str:
    """
    Send a single question using a fresh chat session seeded with the context files.
    Returns the response text or an empty string when no text is returned.
    """
    try:
        chat_session = start_chat_with_context(system_prompt, files)
        response = chat_session.send_message(question)
        return response.text if response and hasattr(response, "text") else ""
    except errors.APIError as exc:
        raise RuntimeError(f"Gemini API error: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to answer question: {exc}") from exc


def create_content_cache(
    pdf_path: Path,
    system_instruction: str,
    model_name: str = MODEL_NAME,
    ttl: str = "3600s",
):
    """
    Create a Gemini content cache with the PDF and system instruction.

    Args:
        pdf_path: Path to the PDF file to cache
        system_instruction: System prompt to include in the cache
        model_name: Model to use (must support caching, e.g. gemini-2.0-flash-001)
        ttl: Time-to-live for the cache (default 1 hour)

    Returns:
        The cached content object from Gemini
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"Context file not found: {pdf_path}")

    client = _get_genai_client()

    uploaded_file = client.files.upload(file=pdf_path)

    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state.name != "ACTIVE":
        raise RuntimeError(f"File upload failed: {uploaded_file.state.name}")

    cached_content = client.caches.create(
        model=model_name,
        config=types.CreateCachedContentConfig(
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(
                            file_uri=uploaded_file.uri,
                            mime_type="application/pdf",
                        ),
                    ],
                )
            ],
            system_instruction=system_instruction,
            display_name="qa-context-cache",
            ttl=ttl,
        ),
    )

    return cached_content


def answer_with_cache(
    question: str,
    cache_name: str,
    model_name: str = MODEL_NAME,
) -> str:
    """
    Answer a question using the cached content.

    Args:
        question: The question to answer
        cache_name: The name of the cached content (from create_content_cache)
        model_name: Model to use (must match the cache model)

    Returns:
        The response text or an empty string
    """
    client = _get_genai_client()

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=question,
            config=types.GenerateContentConfig(
                cached_content=cache_name,
                temperature=GENERATION_CONFIG["temperature"],
                top_p=GENERATION_CONFIG["top_p"],
                top_k=GENERATION_CONFIG["top_k"],
            ),
        )
        return response.text if response and hasattr(response, "text") else ""
    except Exception as exc:
        raise RuntimeError(f"Failed to answer with cache: {exc}") from exc


def delete_cache(cache_name: str) -> None:
    """Delete a content cache when done."""
    client = _get_genai_client()
    try:
        client.caches.delete(name=cache_name)
    except Exception:
        pass  # Ignore errors when deleting cache
