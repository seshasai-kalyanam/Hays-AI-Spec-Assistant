"""
Lightweight OpenRouter client using HTTP requests.

Provides a simple chat completion helper for use in QA generation/evaluation scripts.
"""

import os
import json
from typing import Optional, Dict, Any, List
import warnings

# Suppress Streamlit warnings when running outside of app context
warnings.filterwarnings('ignore', message='.*ScriptRunContext.*')

import requests

# Import from config_vars to match app.py behavior
try:
    from utils.config_vars import (
        MODELS,
        OPENROUTER_CONNECTION_TIMEOUT,
        OPENROUTER_READ_TIMEOUT,
        PROJECT_NAME,
        OPENROUTER_HTTP_REFERER,
    )
except ImportError:
    MODELS = [
        "x-ai/grok-4.3",
        "google/gemini-3.1-flash-lite-preview",
        "google/gemini-3.1-pro-preview",
        "anthropic/claude-opus-4.7",
        "openai/gpt-5.4",
        "openai/gpt-5.5",
    ]
    OPENROUTER_CONNECTION_TIMEOUT = 30
    OPENROUTER_READ_TIMEOUT = 120
    PROJECT_NAME = "hays-Hays"
    OPENROUTER_HTTP_REFERER = "https://hays-brown-specifications-assistant.streamlit.app/"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = MODELS[0]


def _moderation_fallback_model(selected_model: str) -> Optional[str]:
    """Pick a different model from MODELS for moderation retries."""
    for m in MODELS:
        if m != selected_model:
            return m
    return None


def _get_api_key() -> Optional[str]:
    """Resolve the OpenRouter API key from Streamlit secrets only."""
    try:
        import streamlit as st  # type: ignore
        return st.secrets.get("OPENROUTER_API_KEY")
    except Exception:
        return None


def _request_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": OPENROUTER_HTTP_REFERER,
        "X-Title": PROJECT_NAME,
        "Content-Type": "application/json",
    }


def generate_completion(
    system_prompt: str,
    user_message: str,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Call OpenRouter chat completions and return assistant content.

    Raises a RuntimeError with a helpful message if the API key is missing or the request fails.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not configured in st.secrets. Add it to .streamlit/secrets.toml."
        )

    # Use provided model or default
    selected_model = model or DEFAULT_MODEL

    payload: Dict[str, Any] = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "temperature": float(temperature),
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    resp = requests.post(
        OPENROUTER_BASE_URL,
        headers=_request_headers(api_key),
        data=json.dumps(payload),
        timeout=(OPENROUTER_CONNECTION_TIMEOUT, OPENROUTER_READ_TIMEOUT),
    )

    if resp.status_code != 200:
        # Try to surface useful provider messages
        try:
            data = resp.json()
            message = data.get("error") or data
        except Exception:
            message = resp.text

        # Check if this is a moderation error and we haven't already tried the fallback model
        error_str = str(message).lower()
        is_moderation_error = ("moderation" in error_str or
                              "content policy" in error_str or
                              "safety" in error_str)

        fallback_model = _moderation_fallback_model(selected_model)
        if is_moderation_error and fallback_model is not None:
            payload["model"] = fallback_model
            resp = requests.post(
                OPENROUTER_BASE_URL,
                headers=_request_headers(api_key),
                data=json.dumps(payload),
                timeout=(OPENROUTER_CONNECTION_TIMEOUT, OPENROUTER_READ_TIMEOUT),
            )
            if resp.status_code == 200:
                data = resp.json()
                choices: List[Dict[str, Any]] = data.get("choices") or []
                if not choices:
                    raise RuntimeError("OpenRouter response missing choices")
                content = choices[0].get("message", {}).get("content")
                if not content:
                    raise RuntimeError("OpenRouter response contained no content")
                return content
            # If fallback also fails, continue with original error handling

        raise RuntimeError(f"OpenRouter error {resp.status_code}: {message}")

    data = resp.json()
    choices: List[Dict[str, Any]] = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter response missing choices")
    content = choices[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError("OpenRouter response contained no content")
    return content


