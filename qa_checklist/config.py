"""
QA-specific configuration for the checklist multi-model comparison pipeline.

Tests Spec Checklist LLM response quality using curated prompts from
qa_checklist/prompts_curated.csv, mirroring pages/spec_checklist.py PDF batch autofill.
"""

import os

from utils.gemini_prompts import GEMINI_MODELS, SAMPLE_PDF_PATH

# First N rows from prompts_curated.csv (full curated set; use --num-prompts N to override)
QA_NUM_PROMPTS = 20

# Curated checklist prompts CSV (repo-relative)
QA_PROMPTS_CSV = os.path.join("qa_checklist", "prompts_curated.csv")

# Specification PDF for generation (Gemini Files API)
QA_CONTEXT_PDF = SAMPLE_PDF_PATH

# Gemini models to compare (each prompt runs once per model via PDF batch autofill)
QA_COMPARE_MODELS = list(GEMINI_MODELS)

# Per-batch LLM context cache (PDF path, batch payload, raw responses)
QA_CONTEXT_CACHE_JSON = os.path.join("qa_checklist", "qa_llm_context_cache.json")

# Judge model for grading checklist responses (OpenRouter)
QA_JUDGE_MODEL = "google/gemini-3-flash-preview"
QA_JUDGE_FALLBACK_MODELS = [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-opus-4.7",
    "openai/gpt-5.4",
]

# Retry configuration for rate limits (exponential: 1s, 2s, 4s, 8s, …)
MAX_RETRIES = 5
RETRY_BASE_DELAY = 1


def qa_compare_models() -> list[str]:
    """Active comparison models (env QA_COMPARE_MODELS overrides config list)."""
    raw = (os.environ.get("QA_COMPARE_MODELS") or "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    return list(QA_COMPARE_MODELS)


def qa_compare_model_set() -> set[str]:
    return set(qa_compare_models())


def qa_context_pdf_path() -> str:
    """Resolved PDF path (env QA_CONTEXT_PDF overrides config default)."""
    raw = (os.environ.get("QA_CONTEXT_PDF") or "").strip()
    if raw:
        return raw
    return str(QA_CONTEXT_PDF)
