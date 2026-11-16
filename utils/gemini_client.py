"""Shared helpers for the Google Gen AI SDK (`google-genai`)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Generator, Iterable, List

from google import genai
from google.genai import types

from utils.gemini_prompts import (
    CHECKLIST_BATCH_RESPONSE_SCHEMA,
    GEMINI_DEFAULT_MODEL,
    GEMINI_GENERATION_CONFIG,
    GEMINI_PRO_MODEL,
    build_pdf_checklist_batch_system_prompt,
    build_pdf_checklist_single_system_prompt,
    build_checklist_batch_user_message,
    build_checklist_single_user_message,
)

# Models configured in the app that support ThinkingConfig (explicit allowlist).
_CHAT_THINKING_HIGH_MODELS = frozenset({GEMINI_DEFAULT_MODEL, GEMINI_PRO_MODEL})
_CHECKLIST_THINKING_LOW_MODELS = frozenset({GEMINI_PRO_MODEL})
_CHECKLIST_THINKING_MINIMAL_MODELS = frozenset({GEMINI_DEFAULT_MODEL})

DEFAULT_CONTEXT_CACHE_TTL = "900s"


def get_genai_client(api_key: str) -> genai.Client:
    if not api_key:
        raise ValueError("GEMINI_API_KEY is empty")
    return genai.Client(api_key=api_key)


def _normalize_role(role: str) -> str:
    """Gemini contents require 'user' or 'model'; UI code uses 'assistant'."""
    if role == "assistant":
        return "model"
    return role


def _part_from_item(item: Any) -> types.Part:
    if isinstance(item, str):
        return types.Part(text=item)
    uri = getattr(item, "uri", None)
    if uri:
        mime = getattr(item, "mime_type", None) or "application/octet-stream"
        return types.Part.from_uri(file_uri=uri, mime_type=mime)
    raise TypeError(f"Unsupported history part type: {type(item)!r}")


def history_dicts_to_contents(history: List[dict]) -> List[types.Content]:
    """Convert legacy [{'role','parts'}, ...] to google.genai types.Content."""
    out: List[types.Content] = []
    for turn in history:
        role = _normalize_role(turn["role"])
        parts = [_part_from_item(p) for p in turn["parts"]]
        out.append(types.Content(role=role, parts=parts))
    return out


def generation_config_from_dict(
    generation_config: dict,
    *,
    system_instruction: str | None = None,
    response_json_schema: dict | None = None,
    thinking_level: str | None = None,
) -> types.GenerateContentConfig:
    """Build GenerateContentConfig from app/QA-style dict plus optional system instruction."""
    kwargs: dict[str, Any] = {
        "temperature": generation_config["temperature"],
        "top_p": generation_config["top_p"],
        "top_k": generation_config["top_k"],
    }
    if system_instruction is not None:
        kwargs["system_instruction"] = system_instruction
    if response_json_schema is not None:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = response_json_schema
    if thinking_level is not None:
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=thinking_level)
    return types.GenerateContentConfig(**kwargs)


def wait_for_file_active(
    client: genai.Client,
    file: Any,
    *,
    timeout_seconds: int = 300,
    poll_seconds: int = 5,
) -> Any:
    """Poll until a Gemini file is ACTIVE or raise."""
    start = time.time()
    name = file.name
    while True:
        refreshed = client.files.get(name=name)
        state = refreshed.state.name
        if state == "ACTIVE":
            return refreshed
        if state == "FAILED":
            raise RuntimeError(f"Gemini file failed to process: {refreshed.state}")
        if time.time() - start > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for Gemini file {name} to be ACTIVE")
        time.sleep(poll_seconds)


def upload_pdf_path(
    client: genai.Client,
    path: str | Path,
    *,
    mime_type: str = "application/pdf",
) -> Any:
    """Upload a PDF from disk and wait until ACTIVE."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    upload_cfg = types.UploadFileConfig(mime_type=mime_type)
    uploaded = client.files.upload(file=str(path), config=upload_cfg)
    return wait_for_file_active(client, uploaded)


def _pdf_seed_history(files: Iterable[Any]) -> list[dict]:
    """Initial turns that attach PDF(s) for chat sessions."""
    file_list = list(files)
    if not file_list:
        return []
    return [
        {
            "role": "user",
            "parts": [
                *file_list,
                "Analyze these specification PDF documents and keep their content "
                "in context for all following questions.",
            ],
        },
        {
            "role": "model",
            "parts": [
                "I have reviewed the specification PDF(s) and am ready to answer "
                "questions grounded in those documents only.",
            ],
        },
    ]


# Global context cache store for non-Streamlit environments (e.g. scripts, QA)
_GLOBAL_CONTEXT_CACHES: dict[tuple, Any] = {}


def _get_cache_store() -> dict[tuple, Any]:
    """Get the active session-state cache store if in Streamlit, otherwise the global store."""
    try:
        import streamlit as st
        # Initialize in st.session_state if in a Streamlit context
        if "gemini_context_caches" not in st.session_state:
            st.session_state.gemini_context_caches = {}
        return st.session_state.gemini_context_caches
    except Exception:
        return _GLOBAL_CONTEXT_CACHES


def delete_gemini_file(client: genai.Client, gemini_file: Any) -> None:
    """Delete a Gemini File API object; failures are non-fatal."""
    name = getattr(gemini_file, "name", None)
    if not name:
        return
    try:
        client.files.delete(name=name)
    except Exception:
        pass


def _delete_context_cache(client: genai.Client, cache_obj: Any) -> None:
    """Delete a context cache on Gemini servers; failures are non-fatal."""
    name = getattr(cache_obj, "name", None)
    if not name:
        return
    try:
        client.caches.delete(name=name)
    except Exception:
        pass


def _purge_stale_context_caches(
    client: genai.Client,
    store: dict[tuple, Any],
    *,
    keep_key: tuple | None,
) -> None:
    """Remove cached entries whose key no longer matches the active model/files/prompt."""
    for stale_key in list(store.keys()):
        if keep_key is not None and stale_key == keep_key:
            continue
        _delete_context_cache(client, store[stale_key])
        store.pop(stale_key, None)


def purge_all_context_caches(client: genai.Client) -> None:
    """Delete every context cache tracked in the current session/global store."""
    store = _get_cache_store()
    _purge_stale_context_caches(client, store, keep_key=None)


def get_or_create_context_cache(
    client: genai.Client,
    model: str,
    files: Iterable[Any],
    system_instruction: str,
    ttl: str = DEFAULT_CONTEXT_CACHE_TTL,
) -> Any | None:
    """
    Disabled. We rely on Gemini's automatic implicit caching instead.
    """
    return None


def _get_chat_thinking_level(model_name: str) -> str | None:
    """Return 'high' thinking level only for app models known to support ThinkingConfig."""
    if model_name in _CHAT_THINKING_HIGH_MODELS:
        return "high"
    return None


def create_gemini_chat(
    client: genai.Client,
    model: str,
    system_instruction: str,
    files: Iterable[Any],
    prior_turns: list[dict] | None = None,
    *,
    generation_config: dict | None = None,
) -> Any:
    """Create a chat session with PDF context and optional prior turns (using context caching when possible)."""
    gen_cfg = generation_config or GEMINI_GENERATION_CONFIG
    file_list = list(files)
    thinking_level = _get_chat_thinking_level(model)

    # Try utilizing context cache
    cache_obj = get_or_create_context_cache(client, model, file_list, system_instruction)
    if cache_obj is not None:
        try:
            # Build config with caching (system_instruction is baked into the cache)
            config = generation_config_from_dict(
                gen_cfg,
                thinking_level=thinking_level,
            )
            config.cached_content = cache_obj.name

            # No need for the heavy PDF seed turns since files are cached!
            history: list[dict] = []
            for message in prior_turns or []:
                role = message.get("role", "user")
                content = message.get("content", "")
                history.append({"role": role, "parts": [content]})

            return client.chats.create(
                model=model,
                config=config,
                history=history_dicts_to_contents(history),
            )
        except Exception as e:
            print(f"[Gemini Cache] Cached chat session creation failed: {e}. Falling back to standard chat...")

    # Standard non-cached fallback
    history: list[dict] = _pdf_seed_history(file_list)
    for message in prior_turns or []:
        role = message.get("role", "user")
        content = message.get("content", "")
        history.append({"role": role, "parts": [content]})
    config = generation_config_from_dict(
        gen_cfg,
        system_instruction=system_instruction,
        thinking_level=thinking_level,
    )
    return client.chats.create(
        model=model,
        config=config,
        history=history_dicts_to_contents(history),
    )


def chat_with_pdf(
    client: genai.Client,
    model: str,
    system_instruction: str,
    files: Iterable[Any],
    user_text: str,
    *,
    prior_turns: list[dict] | None = None,
) -> str:
    """Single-turn chat over PDF(s); returns full response text."""
    chat = create_gemini_chat(
        client,
        model,
        system_instruction,
        files,
        prior_turns,
    )
    response = chat.send_message(user_text)
    if response and hasattr(response, "text") and response.text:
        return response.text.strip()
    return ""


def stream_chat_reply(chat: Any, user_text: str) -> Generator[str, None, None]:
    """Stream assistant text chunks from an existing chat session."""
    full = ""
    for chunk in chat.send_message_stream(user_text):
        if chunk and hasattr(chunk, "text") and chunk.text:
            full += chunk.text
            yield full


def _get_checklist_thinking_level(model_name: str) -> str | None:
    """Return the lowest supported thinking level for configured checklist models only."""
    if model_name in _CHECKLIST_THINKING_MINIMAL_MODELS:
        return "minimal"
    if model_name in _CHECKLIST_THINKING_LOW_MODELS:
        return "low"
    return None


def _generate_with_pdf_files(
    client: genai.Client,
    model: str,
    system_instruction: str,
    files: Iterable[Any],
    user_text: str,
    *,
    response_json_schema: dict | None = None,
) -> str:
    """One-shot generate_content with PDF file parts + user text (with context caching fallback)."""
    file_list = list(files)
    thinking_level = _get_checklist_thinking_level(model)

    # Try utilizing context cache
    cache_obj = get_or_create_context_cache(client, model, file_list, system_instruction)
    if cache_obj is not None:
        try:
            # Create content config using cache (no system prompt needed as it is in the cache)
            config = generation_config_from_dict(
                GEMINI_GENERATION_CONFIG,
                response_json_schema=response_json_schema,
                thinking_level=thinking_level,
            )
            config.cached_content = cache_obj.name

            # Call API with only user text part (no files)
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=[types.Part(text=user_text)])],
                config=config,
            )
            if response and hasattr(response, "text") and response.text:
                return response.text.strip()
            return ""
        except Exception as e:
            print(f"[Gemini Cache] Cached generation failed: {e}. Falling back to standard generation...")

    # Standard non-cached fallback
    parts: list[types.Part] = [_part_from_item(f) for f in file_list]
    parts.append(types.Part(text=user_text))
    config = generation_config_from_dict(
        GEMINI_GENERATION_CONFIG,
        system_instruction=system_instruction,
        response_json_schema=response_json_schema,
        thinking_level=thinking_level,
    )
    response = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=config,
    )
    if response and hasattr(response, "text") and response.text:
        return response.text.strip()
    return ""


def generate_checklist_single(
    client: genai.Client,
    model: str,
    files: Iterable[Any],
    query: str,
    allowed_answers: str,
    *,
    system_instruction: str | None = None,
) -> str:
    """One checklist field: JSON answer + explanation."""
    sys_prompt = system_instruction or build_pdf_checklist_single_system_prompt()
    user_msg = build_checklist_single_user_message(query, allowed_answers)
    schema = {
        "type": "object",
        "required": ["answer", "explanation"],
        "properties": {
            "answer": {"type": "string"},
            "explanation": {"type": "string"},
        },
    }
    return _generate_with_pdf_files(
        client,
        model,
        sys_prompt,
        files,
        user_msg,
        response_json_schema=schema,
    )


def generate_checklist_batch(
    client: genai.Client,
    model: str,
    files: Iterable[Any],
    fields_payload: list[dict[str, Any]],
    *,
    system_instruction: str | None = None,
) -> str:
    """Up to 10 checklist fields in one call; returns JSON array text."""
    sys_prompt = system_instruction or build_pdf_checklist_batch_system_prompt()
    user_msg = build_checklist_batch_user_message(fields_payload)
    return _generate_with_pdf_files(
        client,
        model,
        sys_prompt,
        files,
        user_msg,
        response_json_schema=CHECKLIST_BATCH_RESPONSE_SCHEMA,
    )


def parse_checklist_batch_response(raw: str) -> list[dict[str, Any]]:
    """Parse batch JSON array from model output."""
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


def init_pdf_session_state() -> None:
    """Initialize Streamlit session keys for uploaded Gemini PDFs."""
    import streamlit as st

    st.session_state.setdefault("gemini_files", [])
    st.session_state.setdefault("gemini_files_by_upload", {})
    st.session_state.setdefault("pdf_viewer_files_by_upload", {})
    loaded_names = st.session_state.setdefault("loaded_pdf_names", [])
    if loaded_names:
        deduped_names: list[str] = []
        seen_names: set[str] = set()
        for name in loaded_names:
            if name and name not in seen_names:
                seen_names.add(name)
                deduped_names.append(name)
        if deduped_names != loaded_names:
            st.session_state.loaded_pdf_names = deduped_names
    st.session_state.setdefault("processed_pdf_names", set())
    st.session_state.setdefault("failed_pdf_uploads", [])
    st.session_state.setdefault("pdf_upload_in_progress", False)
    st.session_state.setdefault("gemini_chat_session", None)


def get_cached_gemini_client():
    """Cached GenAI client for Streamlit (reads GEMINI_API_KEY from st.secrets)."""
    import streamlit as st

    @st.cache_resource
    def _client(api_key: str):
        return get_genai_client(api_key)

    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in Streamlit secrets")
    return _client(str(api_key).strip())


def upload_streamlit_pdf(client: genai.Client, uploaded_file: Any) -> Any:
    """Upload a Streamlit UploadedFile to Gemini; return the first ACTIVE File part."""
    return upload_streamlit_pdf_parts(client, uploaded_file)[0]


def upload_streamlit_pdf_parts(client: genai.Client, uploaded_file: Any) -> list[Any]:
    """Upload a PDF (splitting when over 45 MB); return ACTIVE Gemini file part(s)."""
    import os
    import shutil
    import tempfile

    from utils.pdf_split import split_pdf_for_gemini

    suffix = Path(uploaded_file.name).suffix or ".pdf"
    temp_path: str | None = None
    split_dir: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.getvalue())
            temp_path = tmp.name
        split_dir = tempfile.mkdtemp(prefix="hays_pdf_split_")
        local_paths = split_pdf_for_gemini(Path(temp_path), Path(split_dir))
        upload_cfg = types.UploadFileConfig(mime_type="application/pdf")
        gemini_files: list[Any] = []
        for local_path in local_paths:
            gemini_file = client.files.upload(file=str(local_path), config=upload_cfg)
            gemini_files.append(wait_for_file_active(client, gemini_file))
        if not gemini_files:
            raise RuntimeError("Gemini returned no file handles after upload.")
        return gemini_files
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        if split_dir and os.path.isdir(split_dir):
            shutil.rmtree(split_dir, ignore_errors=True)


