"""User-facing messages for OpenAI, vector-store, and PDF ingestion errors."""

from __future__ import annotations

import openai

from utils.spec_chunking import ScannedPdfError
from utils.vector_store import VectorStoreUnavailable


def format_llm_error(exc: Exception | str) -> str:
    """Plain-language message for a failed upload, chat, or checklist call."""
    err = str(exc).strip()
    lowered = err.lower()

    if isinstance(exc, (ScannedPdfError, VectorStoreUnavailable)):
        return err
    if isinstance(exc, openai.AuthenticationError) or "openai_api_key" in lowered:
        return (
            "OpenAI API key is missing or invalid. "
            "Check **OPENAI_API_KEY** in `.streamlit/secrets.toml`."
        )
    if isinstance(exc, openai.RateLimitError):
        if "insufficient_quota" in lowered or "billing" in lowered:
            return (
                "OpenAI API billing: the account is out of credits or over its quota. "
                "Add credits at [platform.openai.com](https://platform.openai.com/settings/organization/billing) and try again."
            )
        return (
            "OpenAI rate limit reached after several automatic waits. "
            "Wait a minute and upload the PDF again. "
            "If this keeps happening, check the usage limits on your OpenAI account."
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return "OpenAI denied access for this API key or model. Check the key's project and model access."
    if isinstance(exc, openai.NotFoundError) and "model" in lowered:
        return f"This OpenAI model is not available to your API key: {err[:300]}"
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return "Could not reach OpenAI (timeout or network error). Try again."
    if "vector" in lowered and ("extension" in lowered or ("type" in lowered and "does not exist" in lowered)):
        return "The Neon database is missing the pgvector extension. Enable it with: CREATE EXTENSION vector;"
    if ("password" in lowered and "pdf" in lowered) or "pdfium" in lowered:
        return "Could not read this PDF. It may be corrupted or password-protected."
    return f"An error occurred: {err[:500]}"
