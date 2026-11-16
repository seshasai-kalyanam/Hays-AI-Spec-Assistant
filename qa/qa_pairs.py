"""Generate QA pairs using Gemini PDF chat (same path as app.py)."""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

# Fix Unicode encoding issues on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

if not (ROOT_DIR / "qa" / "__init__.py").exists():
    raise RuntimeError(f"qa package not found at {ROOT_DIR / 'qa'}")

from qa.llm_client import configure_client, get_gemini_client
from qa.qa_config import (
    QA_CONTEXT_PDF,
    QA_MODEL,
    QA_NUM_QUESTIONS,
    QA_PAIRS_PATH,
    add_num_prompts_argument,
)
from qa.test_questions import questions_for_testing
from utils.gemini_client import create_gemini_chat, upload_pdf_path
from utils.gemini_prompts import build_pdf_chat_system_prompt


def parse_args() -> argparse.Namespace:
    """CLI for QA pair generation."""
    parser = argparse.ArgumentParser(description="Generate QA pairs from a specification PDF.")
    add_num_prompts_argument(parser)
    return parser.parse_args()


def _load_questions(sample_size: int = QA_NUM_QUESTIONS) -> List[str]:
    """Return the first N questions from qa.test_questions.questions_for_testing."""
    if not questions_for_testing:
        raise ValueError("No questions in qa.test_questions.questions_for_testing")
    n = min(sample_size, len(questions_for_testing))
    return list(questions_for_testing[:n])


def _write_output(items: List[Dict[str, str]]) -> None:
    """Persist accumulated QA items to qa_pairs.json."""
    QA_PAIRS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QA_PAIRS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def _log_question(question: str, idx: int, total: int) -> None:
    """Print the question being processed."""
    question_preview = question[:120] + ("..." if len(question) > 120 else "")
    try:
        print(f"[qa_pairs] >>> Question {idx}/{total}", flush=True)
        print(f"[qa_pairs]  prompt: {question}", flush=True)
        if question != question_preview:
            print(f"[qa_pairs]  preview: {question_preview}", flush=True)
    except UnicodeEncodeError:
        safe_prompt = question.encode("ascii", errors="replace").decode("ascii")
        print(f"[qa_pairs] >>> Question {idx}/{total}", flush=True)
        print(f"[qa_pairs]  prompt: {safe_prompt}", flush=True)


def _log_response(response_text: str, duration: float) -> None:
    """Print response text and timing."""
    try:
        print(f"[qa_pairs]  • Completed in {duration:.2f}s", flush=True)
        print("[qa_pairs]  • Response:", flush=True)
        print(response_text, flush=True)
    except UnicodeEncodeError:
        safe_response = response_text.encode("ascii", errors="replace").decode("ascii")
        print(f"[qa_pairs]  • Completed in {duration:.2f}s", flush=True)
        print("[qa_pairs]  • Response:", flush=True)
        print(safe_response, flush=True)


def _log_progress(idx: int, total: int, start_time: float) -> None:
    """Emit progress heartbeat."""
    elapsed = time.time() - start_time
    print(f"[qa_pairs] Progress: {idx}/{total} saved | elapsed {elapsed:.1f}s", flush=True)


def _create_pdf_chat(client, pdf_file, system_prompt: str):
    """One chat session seeded with the uploaded PDF (no per-question re-upload)."""
    return create_gemini_chat(
        client,
        QA_MODEL,
        system_prompt,
        [pdf_file],
    )


def _send_prompt(chat, question: str) -> str:
    """Send one prompt on an existing PDF chat session."""
    response = chat.send_message(question)
    if response and hasattr(response, "text") and response.text:
        return response.text.strip()
    return ""


def main() -> None:
    args = parse_args()
    if not QA_CONTEXT_PDF.is_file():
        raise FileNotFoundError(f"QA context PDF not found: {QA_CONTEXT_PDF}")

    print(f"[qa_pairs] Using context file: {QA_CONTEXT_PDF}", flush=True)
    print(f"[qa_pairs] Target model: {QA_MODEL}", flush=True)

    configure_client()
    client = get_gemini_client()
    system_prompt = build_pdf_chat_system_prompt()

    prompts = _load_questions(args.num_prompts)
    print(f"[qa_pairs] Running {len(prompts)} questions from qa/test_questions.py", flush=True)

    print("[qa_pairs] Uploading PDF...", flush=True)
    upload_start = time.time()
    pdf_file = upload_pdf_path(client, QA_CONTEXT_PDF)
    print(
        f"[qa_pairs] PDF active ({pdf_file.name}) in {time.time() - upload_start:.1f}s",
        flush=True,
    )

    chat = _create_pdf_chat(client, pdf_file, system_prompt)
    print(
        f"[qa_pairs] Chat session ready; sending {len(prompts)} prompts (single upload)",
        flush=True,
    )

    results: List[Dict[str, str]] = []
    start_time = time.time()

    for idx, question in enumerate(prompts, start=1):
        _log_question(question, idx, len(prompts))
        llm_start = time.time()

        try:
            response_text = _send_prompt(chat, question)
        except Exception as exc:
            response_text = f"[error] {exc}"

        duration = round(time.time() - llm_start, 2)
        _log_response(response_text, duration)

        item: Dict[str, str] = {
            "prompt": question,
            "response": response_text,
            "model": QA_MODEL,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "processing_time_seconds": duration,
            "context_file": str(QA_CONTEXT_PDF),
        }
        results.append(item)
        _write_output(results)
        _log_progress(idx, len(prompts), start_time)

    total = round(time.time() - start_time, 2)
    avg_time = total / len(prompts) if prompts else 0
    print(
        f"[qa_pairs] Completed {len(prompts)} questions in {total:.2f}s "
        f"(avg {avg_time:.2f}s)",
        flush=True,
    )
    print(f"[qa_pairs] Results written to: {QA_PAIRS_PATH}", flush=True)


if __name__ == "__main__":
    main()
