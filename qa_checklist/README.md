# Checklist QA Multi-Model Pipeline

Tests Spec Checklist LLM answer quality by running curated checklist prompts through Gemini PDF batch autofill, mirroring [pages/spec_checklist.py](../pages/spec_checklist.py) auto-fill behavior. OpenRouter judges score the generated answers.

## Overview

1. Load prompts from [prompts_curated.csv](prompts_curated.csv)
2. Upload specification PDF once (default: [sample_pdf/RSE1A IFC EP Design - Electrical SPEC.pdf](../sample_pdf/RSE1A%20IFC%20EP%20Design%20-%20Electrical%20SPEC.pdf))
3. Generate checklist answers in batches of 10 via `run_checklist_batch_autofill` for each Gemini comparison model
4. OpenRouter LLM-judge scores each response (no keyword-extraction judging)
5. Report per-prompt and per-model scores plus a model leaderboard

## Provider split

| Stage | Provider | Secret |
|-------|----------|--------|
| Generation | Gemini (PDF batch) | `GEMINI_API_KEY` |
| Judgment | OpenRouter | `OPENROUTER_API_KEY` |

## Comparison models (default)

- `gemini-3.5-flash`
- `gemini-3.1-pro-preview`

## Usage

```bash
# Default: all 20 prompts x 2 Gemini models (40 rows), deletes existing JSON first
python qa_checklist/qa_pipeline_checklist.py

# Shorter test run (first 4 prompts → 8 rows)
python qa_checklist/qa_pipeline_checklist.py --num-prompts 4 --delete

# Keep prior JSON instead of deleting (still re-runs all prompts/models)
python qa_checklist/qa_pipeline_checklist.py --keep

# Resume: keep JSON and skip prompt x model pairs already completed
python qa_checklist/qa_pipeline_checklist.py --resume

# Override Gemini models or PDF
python qa_checklist/qa_pipeline_checklist.py --models gemini-3.5-flash,gemini-3.1-pro-preview
python qa_checklist/qa_pipeline_checklist.py --pdf sample_pdf/RSE1A IFC EP Design - Electrical SPEC.pdf
```

Individual steps:

```bash
python qa_checklist/qa_generation.py
python qa_checklist/qa_pairs_evaluation.py
python qa_checklist/write_qa_report.py
```

Dashboard:

```bash
streamlit run qa_checklist/app_qa.py
```

## Configuration

Edit [config.py](config.py):

| Setting | Default | Description |
|---------|---------|-------------|
| `QA_NUM_PROMPTS` | `20` | First N rows from curated CSV |
| `QA_COMPARE_MODELS` | Gemini models | Each prompt runs once per model |
| `QA_CONTEXT_PDF` | Sample electrical spec PDF | Attached to Gemini batch calls |
| `QA_PROMPTS_CSV` | `qa_checklist/prompts_curated.csv` | Prompt source |
| `QA_JUDGE_MODEL` | `google/gemini-3-flash-preview` | OpenRouter grading model |

Env overrides (set by `qa_pipeline_checklist.py` CLI): `QA_NUM_PROMPTS`, `QA_CONTEXT_PDF`, `QA_COMPARE_MODELS`.

## Output files

- `qa_pairs.json` — one row per (prompt, model)
- `qa_llm_context_cache.json` — batch payloads and raw responses per run
- `qa_pairs_evaluation.json` — scores + `summary.by_model` + `summary.best_model`
- `qa_report_*.html` — interactive Plotly charts (quality vs cost, leaderboard, heatmap)
- `qa_report_*.md` — text tables and pair details (links to the HTML report)
- `logs/<timestamp>/` — run logs and manifest

## Generation behavior

Matches Spec Checklist PDF batch auto-fill ([pages/spec_checklist.py](../pages/spec_checklist.py)):

- System: `build_pdf_checklist_batch_system_prompt()`
- User: JSON `{"fields": [...]}` with attached PDF file parts
- Batching: up to 10 fields per Gemini API call (`CHECKLIST_BATCH_SIZE`)
- Parsing: `parse_answer_explanation` → `parsed_answer`, `llm_explanation`, `citations`, `field_status`
- **Thinking Config**: To reduce latency/cost and avoid generating `thoughtSignature` metadata, thinking is minimized during batch auto-fill:
  - `"minimal"` thinking for `gemini-3.5-flash` (and other Flash-class models)
  - `"low"` thinking for `gemini-3.1-pro-preview` (lowest level supported by 3.1 Pro)
  - (Standard interactive chat continues to run with `"high"` thinking for deep reasoning)

## Evaluation

Two-part OpenRouter LLM judging per (prompt, model):

1. **Answer score** — parsed answer vs allowed options (PDF-grounded; judge does not receive PDF)
2. **Explanation score** — quality of `llm_explanation` and citations as stated

**Combined score** = average of answer and explanation scores. Summary includes `answer_quality`, `explanation_quality`, `by_model`, and `best_model`. No keyword-extraction judging.
