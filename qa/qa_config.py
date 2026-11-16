"""Shared settings for the QA pipeline (aligned with app PDF chat)."""

from __future__ import annotations

import argparse
from pathlib import Path

from utils.gemini_prompts import GEMINI_DEFAULT_MODEL, SAMPLE_PDF_PATH

QA_NUM_QUESTIONS = 5


def add_num_prompts_argument(parser: argparse.ArgumentParser) -> None:
    """Register -n / --num-prompts on a parser (shared by qa.py and qa_pairs.py)."""
    parser.add_argument(
        "-n",
        "--num-prompts",
        type=parse_num_prompts,
        default=QA_NUM_QUESTIONS,
        metavar="N",
        help=(
            "Number of prompts to run from qa/test_questions.py "
            f"(default: {QA_NUM_QUESTIONS})"
        ),
    )


def parse_num_prompts(value: str) -> int:
    """Parse and validate -n/--num-prompts (argparse passes a string)."""
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not a valid integer: {value!r}") from exc
    if n < 1:
        raise argparse.ArgumentTypeError("num-prompts must be at least 1")
    return n


QA_CONTEXT_PDF = SAMPLE_PDF_PATH
QA_MODEL = GEMINI_DEFAULT_MODEL
QA_DIR = Path(__file__).resolve().parent
QA_PAIRS_PATH = QA_DIR / "qa_pairs.json"
QA_EVALUATION_PATH = QA_DIR / "qa_pairs_evaluation.json"

# OpenRouter judge for qa_pairs_evaluation.py
QA_JUDGE_MODEL = "google/gemini-3-flash-preview"
QA_JUDGE_FALLBACK_MODELS = [
    "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3.1-pro-preview",
    "anthropic/claude-opus-4.7",
    "openai/gpt-5.4",
]
