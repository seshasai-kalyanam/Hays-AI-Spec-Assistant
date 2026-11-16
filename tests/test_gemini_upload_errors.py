"""Unit tests for Gemini upload error messages."""

from __future__ import annotations

from utils.pdf_split import format_gemini_upload_error


def test_billing_exhausted_message():
    err = (
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
        "'message': 'Your prepayment credits are depleted.'}}"
    )
    msg = format_gemini_upload_error(err)
    assert "billing" in msg.lower()
    assert "ai.studio" in msg


def test_missing_api_key_message():
    msg = format_gemini_upload_error(ValueError("GEMINI_API_KEY not found in Streamlit secrets"))
    assert "GEMINI_API_KEY" in msg
    assert "secrets.toml" in msg


def test_timeout_message():
    msg = format_gemini_upload_error(
        TimeoutError("Timed out waiting for Gemini file files/abc to be ACTIVE")
    )
    assert "too long" in msg.lower()


def test_gemini_file_processing_failed():
    msg = format_gemini_upload_error(RuntimeError("Gemini file failed to process: FAILED"))
    assert "could not process" in msg.lower()
