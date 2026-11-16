"""
Fetch OpenRouter model metadata (including per-token pricing) and cache locally.

API: https://openrouter.ai/docs/api/api-reference/models/get-models

Usage:
    python qa_checklist/model_pricing.py
    python qa_checklist/model_pricing.py --output qa_checklist/model_pricing.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_OUTPUT = os.path.join(SCRIPT_DIR, "model_pricing.json")
TOKENS_PER_MILLION = 1_000_000

# Rates when OpenRouter cache is stale or a model is not listed (USD per 1M tokens).
MANUAL_MODEL_PRICING: dict[str, dict[str, Any]] = {
    "google/gemini-3.5-flash": {
        "id": "google/gemini-3.5-flash",
        "name": "Google: Gemini 3.5 Flash",
        "prompt_per_token_usd": 1.5e-06,
        "completion_per_token_usd": 9e-06,
        "prompt_per_million_usd": 1.5,
        "completion_per_million_usd": 9.0,
        "blended_per_million_usd": 5.25,
    },
    "google/gemini-3.1-pro-preview": {
        "id": "google/gemini-3.1-pro-preview",
        "name": "Google: Gemini 3.1 Pro Preview",
        "prompt_per_token_usd": 2e-06,
        "completion_per_token_usd": 1.2e-05,
        "prompt_per_million_usd": 2.0,
        "completion_per_million_usd": 12.0,
        "blended_per_million_usd": 7.0,
    },
}


def _get_api_key() -> Optional[str]:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    try:
        import streamlit as st  # type: ignore

        key = st.secrets.get("OPENROUTER_API_KEY")
        if key:
            return str(key).strip()
    except Exception:
        pass
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from utils.streamlit_secrets import get_secret

        key = get_secret("OPENROUTER_API_KEY", repo_root=PROJECT_ROOT)
        if key:
            return str(key).strip()
    except Exception:
        pass
    return None


def _parse_price_per_token(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _per_million_usd(per_token_usd: Optional[float]) -> Optional[float]:
    if per_token_usd is None:
        return None
    return per_token_usd * TOKENS_PER_MILLION


def _blended_per_million(prompt: Optional[float], completion: Optional[float]) -> Optional[float]:
    if prompt is None and completion is None:
        return None
    if prompt is None:
        return completion
    if completion is None:
        return prompt
    return (prompt + completion) / 2.0


def fetch_openrouter_models(api_key: str, timeout: int = 30) -> list[dict[str, Any]]:
    resp = requests.get(
        OPENROUTER_MODELS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise RuntimeError(f"OpenRouter models API HTTP {resp.status_code}: {detail}")
    data = resp.json() or {}
    models = data.get("data")
    if not isinstance(models, list):
        raise RuntimeError("OpenRouter models API response missing 'data' list")
    return models


def model_entry_from_api_row(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    model_id = row.get("id")
    if not model_id:
        return None
    pricing = row.get("pricing") or {}
    prompt_token = _parse_price_per_token(pricing.get("prompt"))
    completion_token = _parse_price_per_token(pricing.get("completion"))
    prompt_m = _per_million_usd(prompt_token)
    completion_m = _per_million_usd(completion_token)
    return {
        "id": model_id,
        "name": row.get("name") or model_id,
        "prompt_per_token_usd": prompt_token,
        "completion_per_token_usd": completion_token,
        "prompt_per_million_usd": prompt_m,
        "completion_per_million_usd": completion_m,
        "blended_per_million_usd": _blended_per_million(prompt_m, completion_m),
    }


def merge_manual_model_pricing(cache: dict[str, Any]) -> None:
    models = cache.setdefault("models", {})
    if not isinstance(models, dict):
        cache["models"] = {}
        models = cache["models"]
    for model_id, entry in MANUAL_MODEL_PRICING.items():
        models[model_id] = dict(entry)


def build_pricing_cache(models: list[dict[str, Any]]) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for row in models:
        entry = model_entry_from_api_row(row)
        if entry:
            entries[entry["id"]] = entry
    cache = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": OPENROUTER_MODELS_URL,
        "price_unit": "USD per 1M tokens (OpenRouter public pricing; converted from per-token rates)",
        "blended_note": "blended_per_million_usd = average of prompt and completion $/1M token rates",
        "models": entries,
    }
    merge_manual_model_pricing(cache)
    return cache


def write_pricing_json(cache: dict[str, Any], output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)
        f.write("\n")


def load_pricing_json(path: Optional[str] = None) -> dict[str, Any]:
    pricing_path = path or DEFAULT_OUTPUT
    with open(pricing_path, encoding="utf-8") as f:
        cache = json.load(f)
    merge_manual_model_pricing(cache)
    return cache


def lookup_model_pricing(
    model_id: str,
    cache: dict[str, Any],
) -> Optional[dict[str, Any]]:
    models = cache.get("models") or {}
    if model_id in models:
        return models[model_id]
    if "/" not in model_id:
        prefixed = f"google/{model_id}"
        if prefixed in models:
            return models[prefixed]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OpenRouter model pricing into JSON cache")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    api_key = _get_api_key()
    if not api_key:
        print(
            "[model_pricing] OPENROUTER_API_KEY not found. "
            "Set env var or add to .streamlit/secrets.toml.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"[model_pricing] Fetching models from {OPENROUTER_MODELS_URL}")
    rows = fetch_openrouter_models(api_key)
    cache = build_pricing_cache(rows)
    write_pricing_json(cache, args.output)
    n = len(cache.get("models") or {})
    print(f"[model_pricing] Wrote {n} models to {args.output}")


if __name__ == "__main__":
    main()
