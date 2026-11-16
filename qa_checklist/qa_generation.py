"""
Generate checklist QA pairs from prompts_curated.csv using PDF batch autofill.

Mirrors pages/spec_checklist.py auto-fill:
- upload_pdf_path once per run
- run_checklist_batch_autofill in batches of CHECKLIST_BATCH_SIZE (10)
- Gemini models only (see qa_checklist/config.py)

Writes qa_checklist/qa_pairs.json incrementally (one row per prompt x model).
Writes qa_checklist/qa_llm_context_cache.json (one row per batch x model).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import warnings
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")
logging.getLogger("streamlit").setLevel(logging.CRITICAL)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from qa_checklist.qa_verbose import configure_unbuffered_stdout, verbose_block, verbose_log
    from qa_checklist.config import (
        QA_CONTEXT_CACHE_JSON,
        QA_NUM_PROMPTS,
        QA_PROMPTS_CSV,
        MAX_RETRIES,
        RETRY_BASE_DELAY,
        qa_compare_models,
        qa_context_pdf_path,
    )
except Exception:
    from qa_verbose import configure_unbuffered_stdout, verbose_block, verbose_log
    from config import (
        QA_CONTEXT_CACHE_JSON,
        QA_NUM_PROMPTS,
        QA_PROMPTS_CSV,
        MAX_RETRIES,
        RETRY_BASE_DELAY,
        qa_compare_models,
        qa_context_pdf_path,
    )

from utils.checklist_schema import ChecklistField, load_prompts_csv
from utils.gemini_client import get_genai_client, upload_pdf_path
from utils.gemini_prompts import (
    CHECKLIST_BATCH_SIZE,
    build_pdf_checklist_batch_system_prompt,
    fields_to_batch_payload,
)
from utils.results_form import _resolve_gemini_api_key, run_checklist_batch_autofill


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


QA_NUM_PROMPTS = _env_int("QA_NUM_PROMPTS", QA_NUM_PROMPTS)
COMPARE_MODELS = qa_compare_models()
OUTPUT_JSON = os.path.join(CURRENT_DIR, "qa_pairs.json")
CONTEXT_CACHE_JSON = (
    QA_CONTEXT_CACHE_JSON
    if os.path.isabs(QA_CONTEXT_CACHE_JSON)
    else os.path.join(PROJECT_ROOT, QA_CONTEXT_CACHE_JSON)
)


def _resume_enabled() -> bool:
    return os.environ.get("QA_RESUME", "").strip().lower() in ("1", "true", "yes")


def _pair_key(prompt_number: Any, model: str) -> tuple[Any, str]:
    return (prompt_number, str(model or ""))


def _load_json_list(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _done_pair_keys(pairs: list[dict[str, Any]]) -> set[tuple[Any, str]]:
    keys: set[tuple[Any, str]] = set()
    for item in pairs:
        model = item.get("model")
        if model:
            keys.add(_pair_key(item.get("prompt_number"), model))
    return keys


def _filter_pairs_for_compare_models(
    pairs: list[dict[str, Any]],
    models: list[str],
) -> list[dict[str, Any]]:
    allowed = set(models)
    return [p for p in pairs if str(p.get("model") or "") in allowed]


def _write_json_list(path: str, items: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _load_context_cache(pdf_path: str) -> dict[str, Any]:
    if os.path.exists(CONTEXT_CACHE_JSON):
        try:
            with open(CONTEXT_CACHE_JSON, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
        except Exception:
            pass
    return _init_context_cache(pdf_path)


def _init_context_cache(pdf_path: str) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone(timedelta(hours=-6))).isoformat(),
        "pdf_path": pdf_path,
        "batch_size": CHECKLIST_BATCH_SIZE,
        "system_prompt": build_pdf_checklist_batch_system_prompt(),
        "entries": [],
    }


def _write_context_cache(cache: dict[str, Any]) -> None:
    with open(CONTEXT_CACHE_JSON, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _append_context_cache_entry(
    cache: dict[str, Any],
    *,
    batch_index: int,
    model: str,
    fields: list[ChecklistField],
    fields_payload: dict[str, Any],
    raw_batch_response: str = "",
    error: str = "",
) -> None:
    cache["entries"].append(
        {
            "batch_index": batch_index,
            "model": model,
            "prompt_numbers": [f.prompt_number for f in fields],
            "fields_payload": fields_payload,
            "raw_batch_response": raw_batch_response,
            "error": error,
            "timestamp": datetime.now(timezone(timedelta(hours=-6))).isoformat(),
        }
    )
    _write_context_cache(cache)


def _append_json_item(path: str, item: dict[str, Any]) -> None:
    existing: list[dict[str, Any]] = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing.append(item)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _resolve_prompts_csv() -> str:
    if os.path.isabs(QA_PROMPTS_CSV):
        return QA_PROMPTS_CSV
    return os.path.join(PROJECT_ROOT, QA_PROMPTS_CSV)


def _resolve_pdf_path() -> Path:
    raw = qa_context_pdf_path()
    path = Path(raw)
    if not path.is_file():
        raise FileNotFoundError(f"QA context PDF not found: {path}")
    return path


def _chunk_fields(fields: list[ChecklistField], size: int) -> list[list[ChecklistField]]:
    return [fields[i : i + size] for i in range(0, len(fields), size)]


def _pending_fields_for_model(
    fields: list[ChecklistField],
    model: str,
    done_keys: set[tuple[Any, str]],
) -> list[ChecklistField]:
    return [
        f for f in fields if _pair_key(f.prompt_number, model) not in done_keys
    ]


def _build_record_from_batch_result(
    field: ChecklistField,
    batch_result: dict[str, Any],
    *,
    model: str,
    context_pdf: str,
    error: str = "",
) -> dict[str, Any]:
    return {
        "prompt_number": field.prompt_number,
        "category": field.category,
        "prompt": field.prompt,
        "possible_answers": field.possible_answers_raw,
        "allowed_options": field.allowed_options,
        "field_kind": field.field_kind,
        "model": model,
        "response": batch_result.get("response", ""),
        "parsed_answer": batch_result.get("parsed_answer", ""),
        "llm_explanation": batch_result.get("llm_explanation", ""),
        "parse_valid": batch_result.get("parse_valid", False),
        "parse_note": batch_result.get("parse_note", "") or error,
        "field_status": batch_result.get("field_status", ""),
        "status_reason": batch_result.get("status_reason", ""),
        "citations": batch_result.get("citations", []),
        "context_pdf": context_pdf,
        "timestamp": datetime.now(timezone(timedelta(hours=-6))).isoformat(),
    }


def _build_error_record(
    field: ChecklistField,
    model: str,
    *,
    context_pdf: str,
    error: str,
) -> dict[str, Any]:
    return _build_record_from_batch_result(
        field,
        {
            "response": f"<error: {error}>",
            "parsed_answer": "Not specified",
            "llm_explanation": "",
            "parse_valid": False,
            "parse_note": error,
            "field_status": "error",
            "status_reason": error,
            "citations": [],
        },
        model=model,
        context_pdf=context_pdf,
        error=error,
    )


def _run_batch_with_retries(
    batch: list[ChecklistField],
    pdf_files: list[Any],
    model: str,
) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for retry in range(MAX_RETRIES):
        try:
            if retry > 0:
                delay = RETRY_BASE_DELAY * (2 ** (retry - 1))
                verbose_log(
                    "qa_gen",
                    f"    Batch retry {retry}/{MAX_RETRIES - 1} ({model}) after {delay}s...",
                )
                time.sleep(delay)
            verbose_log("qa_gen", f"    Calling Gemini batch ({model}, {len(batch)} field(s))...")
            return run_checklist_batch_autofill(batch, pdf_files, model=model)
        except Exception as e:
            last_err = e
            err = str(e).lower()
            if "rate" in err or "429" in err or "resource_exhausted" in err:
                continue
            raise
    raise last_err or RuntimeError(f"Batch autofill failed for model {model}")


def _log_batch_results(results: list[dict[str, Any]], elapsed_s: float) -> None:
    verbose_log("qa_gen", f"    Batch OK in {elapsed_s:.1f}s ({len(results)} result(s))")
    for result in results:
        verbose_log(
            "qa_gen",
            f"      #{result.get('row_index')} parsed={result.get('parsed_answer')!r} "
            f"valid={result.get('parse_valid')}",
        )
        if result.get("llm_explanation"):
            verbose_block("qa_gen", "Explanation", str(result.get("llm_explanation")))


def _generate_and_write(
    fields: list[ChecklistField],
    pdf_files: list[Any],
    pdf_path: str,
    models: list[str],
    context_cache: dict[str, Any],
    *,
    done_keys: set[tuple[Any, str]],
) -> tuple[int, int]:
    """Return (skipped_count, generated_count)."""
    skipped = 0
    generated = 0
    batch_counter = 0

    for midx, model in enumerate(models, start=1):
        pending = _pending_fields_for_model(fields, model, done_keys)
        if not pending:
            skipped += len(fields)
            verbose_log("qa_gen", f"Model [{midx}/{len(models)}]: {model} — all pairs done, skip")
            continue

        verbose_log(
            "qa_gen",
            f"Model [{midx}/{len(models)}]: {model} — {len(pending)} pending field(s)",
        )
        batches = _chunk_fields(pending, CHECKLIST_BATCH_SIZE)

        for bidx, batch in enumerate(batches, start=1):
            batch_counter += 1
            prompt_nums = [f.prompt_number for f in batch]
            verbose_log(
                "qa_gen",
                f"  Batch [{bidx}/{len(batches)}] prompts {prompt_nums}",
            )
            for field in batch:
                verbose_block("qa_gen", f"Prompt #{field.prompt_number}", field.prompt)

            payload = fields_to_batch_payload(batch)
            t0 = time.time()

            try:
                results = _run_batch_with_retries(batch, pdf_files, model)
                _log_batch_results(results, time.time() - t0)
                _append_context_cache_entry(
                    context_cache,
                    batch_index=batch_counter,
                    model=model,
                    fields=batch,
                    fields_payload=payload,
                    raw_batch_response=json.dumps(results, ensure_ascii=False),
                )
                results_by_row = {int(r.get("row_index")): r for r in results}
                for field in batch:
                    result = results_by_row.get(field.row_index, {})
                    item = _build_record_from_batch_result(
                        field,
                        result,
                        model=model,
                        context_pdf=pdf_path,
                    )
                    _append_json_item(OUTPUT_JSON, item)
                    done_keys.add(_pair_key(field.prompt_number, model))
                    generated += 1
            except Exception as e:
                err = str(e)
                verbose_log("qa_gen", f"    FAIL batch: {err[:200]}")
                _append_context_cache_entry(
                    context_cache,
                    batch_index=batch_counter,
                    model=model,
                    fields=batch,
                    fields_payload=payload,
                    error=err,
                )
                for field in batch:
                    item = _build_error_record(
                        field,
                        model,
                        context_pdf=pdf_path,
                        error=err,
                    )
                    _append_json_item(OUTPUT_JSON, item)
                    done_keys.add(_pair_key(field.prompt_number, model))
                    generated += 1

    return skipped, generated


def main() -> None:
    configure_unbuffered_stdout()
    verbose_log("qa_gen", "=" * 60)
    verbose_log("qa_gen", "CHECKLIST QA GENERATION - PDF batch autofill")
    verbose_log("qa_gen", "=" * 60)

    csv_path = _resolve_prompts_csv()
    pdf_path = _resolve_pdf_path()
    verbose_log("qa_gen", f"  Prompts CSV: {csv_path}")
    verbose_log("qa_gen", f"  Prompt limit: {QA_NUM_PROMPTS}")
    verbose_log("qa_gen", f"  Context PDF: {pdf_path}")
    verbose_log("qa_gen", f"  Batch size: {CHECKLIST_BATCH_SIZE}")
    verbose_log("qa_gen", f"  Compare models ({len(COMPARE_MODELS)}): {COMPARE_MODELS}")
    verbose_log("qa_gen", "")

    fields = load_prompts_csv(csv_path, limit=QA_NUM_PROMPTS)
    verbose_log("qa_gen", f"Loaded {len(fields)} checklist field(s)")
    resume = _resume_enabled()
    if resume:
        verbose_log("qa_gen", "  Mode: RESUME (skip completed prompt x model pairs)")
    verbose_log("qa_gen", "")

    existing_pairs = _load_json_list(OUTPUT_JSON) if resume else []
    if resume:
        before = len(existing_pairs)
        existing_pairs = _filter_pairs_for_compare_models(existing_pairs, COMPARE_MODELS)
        pruned = before - len(existing_pairs)
        done_keys = _done_pair_keys(existing_pairs)
        _write_json_list(OUTPUT_JSON, existing_pairs)
        verbose_log(
            "qa_gen",
            f"Resuming {OUTPUT_JSON}: {len(existing_pairs)} row(s) for "
            f"{len(COMPARE_MODELS)} compare model(s), "
            f"{len(done_keys)} completed pair(s)"
            + (f", pruned {pruned} row(s) outside compare list" if pruned else ""),
        )
    else:
        done_keys = _done_pair_keys(existing_pairs)
        _write_json_list(OUTPUT_JSON, [])
        verbose_log("qa_gen", f"Initialized {OUTPUT_JSON}")

    if resume:
        context_cache = _load_context_cache(str(pdf_path))
        context_cache["pdf_path"] = str(pdf_path)
        _write_context_cache(context_cache)
        verbose_log(
            "qa_gen",
            f"Loaded {CONTEXT_CACHE_JSON} ({len(context_cache.get('entries') or [])} cached batch(es))",
        )
    else:
        context_cache = _init_context_cache(str(pdf_path))
        _write_context_cache(context_cache)
        verbose_log("qa_gen", f"Initialized {CONTEXT_CACHE_JSON}")
    verbose_log("qa_gen", "")

    verbose_log("qa_gen", "Uploading PDF to Gemini...")
    client = get_genai_client(_resolve_gemini_api_key())
    t_upload = time.time()
    pdf_file = upload_pdf_path(client, pdf_path)
    verbose_log(
        "qa_gen",
        f"  PDF uploaded ({pdf_file.state.name}) in {time.time() - t_upload:.1f}s",
    )
    verbose_log("qa_gen", "")

    expected = len(fields) * len(COMPARE_MODELS)
    t0 = time.time()
    skipped, generated = _generate_and_write(
        fields,
        [pdf_file],
        str(pdf_path),
        COMPARE_MODELS,
        context_cache,
        done_keys=done_keys,
    )
    elapsed = time.time() - t0
    final_count = len(_load_json_list(OUTPUT_JSON))

    verbose_log("qa_gen", "=" * 60)
    verbose_log("qa_gen", "GENERATION COMPLETE")
    verbose_log("qa_gen", f"  Fields: {len(fields)} x {len(COMPARE_MODELS)} models = {expected} expected rows")
    verbose_log("qa_gen", f"  Skipped (resume): {skipped}, generated this run: {generated}, total rows: {final_count}")
    verbose_log("qa_gen", f"  Total time: {elapsed:.1f}s")
    verbose_log("qa_gen", f"  Output: {OUTPUT_JSON}")
    verbose_log("qa_gen", f"  Context cache: {CONTEXT_CACHE_JSON} ({len(context_cache.get('entries') or [])} batches)")
    verbose_log("qa_gen", "=" * 60)


if __name__ == "__main__":
    main()
