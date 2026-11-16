# Hays Specifications Assistant

A Streamlit application for querying and reviewing construction specification PDFs for electrical and related building projects. The app is grounded in the PDFs uploaded during the session through Gemini's PDF file workflow; it does not use a local vector knowledge base.

## App Functionality

### Specifications Chat

The main page, `app.py`, is the PDF-grounded chat interface.

- Upload one or more specification PDFs on the Home page. The same uploaded PDFs are shared with the checklist page.
- Ask free-form questions against the uploaded PDFs. The default model is `gemini-3.5-flash`; `gemini-3.1-pro-preview` is available from Settings for deeper reasoning.
- Use built-in follow-up actions for common construction workflows: draft email, draft RFI, make checklist, show source excerpts, and find related sections.
- Open cited pages in the right-column PDF viewer. Source lines include a **View page** action that jumps to that page in the embedded viewer, with page navigation and zoom controls. The viewer is implemented in `utils/pdf_citation_viewer.py` and uses [`streamlit-pdf-viewer`](https://pypi.org/project/streamlit-pdf-viewer/).
- Save useful assistant replies into Collected Responses, edit them, and export the collection as Markdown or Word.
- Reopen recent chat threads, create a new chat, rename threads inline, and soft-delete threads from visible history.
- Submit helpful / not helpful feedback for responses. Feedback and prompt logging are stored in Neon PostgreSQL when logging is configured.

### Spec. Compliance Review Checklist

The checklist page, `pages/spec_checklist.py`, turns the uploaded PDFs into a structured Hays checklist workflow.

- Loads approximately 384 checklist prompts from `db/prompts.csv`, aligned to the official Hays Word checklist template.
- Lets users search, filter, and select checklist fields for auto-fill.
- Sends selected prompts to Gemini in batches of 10 using the same uploaded PDFs as context.
- Skips previously processed checked rows on later runs. Clear a field to reset its value, status, footnotes, and parse warnings before re-running it.
- Tracks row statuses such as answered, Needs review, Not found in specification, and error.
- Shows the last auto-fill run with per-row citations, explanations, raw model JSON, and processing status.
- Supports manual edits after auto-fill. Manual edits clear prior auto-fill footnotes for that row.
- Exports the current checklist as Word (`.docx`) using the official template, or as Markdown in full, answered-only, or not-in-specification formats.

### Authentication, Secrets, And Logging

- Auth0 login is required, with verified email and allowed-domain checks.
- `GEMINI_API_KEY` is required for chat and checklist generation.
- `OPENROUTER_API_KEY` is required for the QA evaluation pipelines.
- Neon PostgreSQL is optional for prompt and feedback logging.
- Local secrets are read from `.streamlit/secrets.toml` or the environment, depending on the script.

## Testing And QA Pipelines

The repository has two LLM QA pipelines. Both generate model responses, evaluate them with an LLM judge, and write reports, but they test different app surfaces.

### 1. Specification Chat QA (`qa/`)

This pipeline tests the main chat behavior in `app.py`.

It runs:

1. `qa/qa_pairs.py` uploads the sample PDF once, creates a Gemini chat session with the same PDF-chat system prompt used by the app, and sends prompts from `qa/test_questions.py`.
2. `qa/qa_pairs_evaluation.py` scores each response with an OpenRouter judge using a 0.0-1.0 rubric for correctness, concision, and citation quality.
3. `qa/write_qa_report.py` writes a timestamped Markdown report with summary statistics, generated answers, judge explanations, and AI-generated prompt improvement suggestions.

Run the full chat QA pipeline:

```bash
python qa/qa.py
```

Run a smaller sample:

```bash
python qa/qa.py --num-prompts 2
```

Outputs:

- `qa/qa_pairs.json` contains generated prompt/response pairs.
- `qa/qa_pairs_evaluation.json` contains judge scores and summary statistics.
- `qa/qa_report_*.md` contains the human-readable report.

### 2. Checklist Multi-Model QA (`qa_checklist/`)

This pipeline tests the checklist auto-fill behavior in `pages/spec_checklist.py`.

It runs:

1. `qa_checklist/qa_generation.py` loads curated checklist prompts from `qa_checklist/prompts_curated.csv`, attaches the sample specification PDF, and runs checklist batch auto-fill for each configured Gemini model.
2. `qa_checklist/qa_pairs_evaluation.py` grades each prompt/model result with OpenRouter using separate answer-quality and explanation-quality judgments, then combines the scores.
3. `qa_checklist/write_qa_report.py` writes Markdown and interactive HTML reports with per-prompt scores, per-model summaries, model leaderboard, quality/cost charts, and heatmaps.

Run the default checklist comparison:

```bash
python qa_checklist/qa_pipeline_checklist.py
```

Useful options:

```bash
python qa_checklist/qa_pipeline_checklist.py --num-prompts 4 --delete
python qa_checklist/qa_pipeline_checklist.py --resume
python qa_checklist/qa_pipeline_checklist.py --models gemini-3.5-flash,gemini-3.1-pro-preview
python qa_checklist/qa_pipeline_checklist.py --pdf "sample_pdf/RSE1A IFC EP Design - Electrical SPEC.pdf"
python qa_checklist/qa_pipeline_checklist.py --regression-threshold 0.05
```

Default behavior deletes existing checklist QA JSON artifacts and runs 20 curated prompts across the configured comparison models. Use `--keep` to preserve JSON and re-run, or `--resume` to skip prompt/model pairs already present in `qa_checklist/qa_pairs.json`.

Outputs:

- `qa_checklist/qa_pairs.json` contains one row per prompt/model generation result.
- `qa_checklist/qa_llm_context_cache.json` stores batch payloads and raw responses for debugging.
- `qa_checklist/qa_pairs_evaluation.json` contains answer, explanation, combined scores, per-model summaries, and best-model metadata.
- `qa_checklist/qa_report_*.md` contains the text report.
- `qa_checklist/qa_report_*.html` contains interactive Plotly charts.
- `qa_checklist/logs/<timestamp>/` contains subprocess logs and `manifest.json` for each full pipeline run.

## Notes For Interpreting QA Results

- The generation stage uses Gemini with the uploaded/sample PDF attached.
- The judge stage does not receive the PDF. It scores the generated answer, explanation, and citations as stated.
- Low scores should be triaged as either prompt/system-prompt issues, missing or weak citations, parse failures, wrong allowed-answer selection, or actual model grounding failures.
- Generated JSON, reports, logs, caches, and `__pycache__` directories are run artifacts; review them before committing.
