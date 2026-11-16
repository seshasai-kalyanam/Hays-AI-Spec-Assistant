"""LLM-backed evaluation of qa_pairs.json outputs (OpenRouter judge)."""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Add parent directory to path for imports
# This ensures imports work when script is run directly or as subprocess
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Verify the path is set correctly
if not (ROOT_DIR / "qa" / "__init__.py").exists():
    raise RuntimeError(f"qa package not found at {ROOT_DIR / 'qa'}")

from qa.qa_config import (  # type: ignore
    QA_EVALUATION_PATH,
    QA_JUDGE_FALLBACK_MODELS,
    QA_JUDGE_MODEL,
    QA_PAIRS_PATH,
)
from qa_checklist.llm_client import generate_completion  # type: ignore

OUTPUT_PATH = QA_EVALUATION_PATH
DEFAULT_WORD_LIMIT = 50

_judge_override = (os.environ.get("QA_JUDGE_MODEL_OVERRIDE") or "").strip()
EVALUATION_MODEL = _judge_override or QA_JUDGE_MODEL

# Focus the model on concise, citation-aware scoring
EVALUATION_SYSTEM_PROMPT = """
You are an impartial evaluator of question/answer pairs for electrical construction specifications.

Scoring rubric (0.0-1.0):
- 0.90-1.00: Direct, correct, concise; aligned with standard electrical construction terminology. Includes a clear citation marker
  (preferred) or is evidently correct/plausible; no fabrication. Missing
  citation alone should reduce by at most ~0.05.
- 0.75-0.89: Mostly correct and concise with minor omissions, small wording
  issues, or missing citation.
- 0.50-0.74: Partially correct or incomplete; some vagueness or minor
  speculation.
- 0.25-0.49: Weak, tangential, or contains noticeable speculation.
- 0.00-0.24: Off-topic, contradictory to the prompt/context, or clearly
  fabricated.

When no supporting context is provided, judge plausibility and clarity assuming an electrical construction context; do not
assume incorrect solely because a citation is missing.
If the response clearly states no relevant information was found and avoids
fabrication, score it as 1.0.
Keep explanations under 25 words.
Return only JSON: {"score": <float>, "explanation": "<brief reason>"}.
""".strip()


def _configure_utf8_output() -> None:
    """Ensure stdout/stderr can emit Unicode characters."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                # If reconfigure is unavailable or fails, skip silently.
                pass


def _load_pairs() -> List[Dict[str, Any]]:
    """Load generated QA pairs from disk."""
    with open(QA_PAIRS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_eval_prompt(prompt: str, response: str, word_limit: int) -> str:
    """Construct an evaluation prompt that asks for a JSON score."""
    truncated_prompt = prompt if len(prompt) <= 2000 else (prompt[:2000] + "... [truncated]")
    safe_response = response if response is not None else ""
    truncated_response = safe_response if len(safe_response) <= 4000 else (safe_response[:4000] + "... [truncated]")

    return (
        "Evaluate the assistant response using the rubric above.\n"
        f"Word limit target: {word_limit} (soft cushion of +10 words is acceptable; do not over-penalize slight overruns when the answer is strong).\n"
        "Consider prompt coverage, correctness, conciseness, and citation quality. A missing 'Source:' citation should be a small deduction only if the answer is otherwise solid.\n"
        "Prefer partial credit over zeros; reserve very low scores for off-topic, contradictory, or clearly fabricated content.\n"
        "If no context is available, judge plausibility and clarity rather than assuming the answer is wrong.\n"
        "If the response explicitly states that no relevant information was found and avoids fabrication, score 1.0.\n"
        "Return only JSON with keys 'score' and 'explanation'.\n\n"
        f"Prompt:\n{truncated_prompt}\n\nResponse:\n{truncated_response}"
    )


def _is_error_response(response: str) -> bool:
    """Heuristically detect error placeholder responses."""
    lowered = (response or "").strip().lower()
    return lowered.startswith("[error") or lowered.startswith("error:")


def _judge_models() -> List[str]:
    """Primary judge plus fallbacks (deduplicated, order preserved)."""
    seen: set[str] = set()
    candidates: List[str] = []
    for model in [EVALUATION_MODEL, *QA_JUDGE_FALLBACK_MODELS]:
        if model and model not in seen:
            seen.add(model)
            candidates.append(model)
    return candidates


def _parse_score(content: str) -> Tuple[float, str]:
    """Parse model output to a bounded score and explanation."""
    def _clamp(score_val: Any) -> float:
        try:
            score = float(score_val)
        except Exception:
            return 0.0
        return max(0.0, min(1.0, score))

    candidates = [content]
    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            score = _clamp(data.get("score", 0))
            explanation = str(data.get("explanation", ""))
            return score, explanation
        except Exception:
            pass

    # Last-attempt: extract outer JSON braces
    try:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            data = json.loads(stripped[start : end + 1])
            score = _clamp(data.get("score", 0))
            explanation = str(data.get("explanation", ""))
            return score, explanation
    except Exception:
        pass

    return 0.0, "Failed to parse evaluator output as JSON"


def _evaluate_with_retries(prompt: str) -> Tuple[float, str, float, str]:
    """Call OpenRouter with retry and fallback model handling."""
    start = time.time()
    last_error = ""

    for model in _judge_models():
        delay = 1.0
        for _attempt in range(3):
            try:
                text = generate_completion(
                    EVALUATION_SYSTEM_PROMPT,
                    prompt,
                    model=model,
                    temperature=0.3,
                )
                score, explanation = _parse_score(text)
                return score, explanation, time.time() - start, model
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                lower = last_error.lower()
                if "429" in lower or "rate limit" in lower:
                    time.sleep(delay)
                    delay = min(delay * 2, 8)
                    continue
                if "403" in lower or "moderation" in lower:
                    break
                time.sleep(0.5)

    return 0.0, f"Evaluation failed: {last_error}", time.time() - start, ""


def _score_distribution(scores: List[float]) -> Dict[str, Any]:
    """Compute percentile and distribution stats for response scores."""
    if not scores:
        return {
            "count": 0,
            "average_score": 0.0,
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "score_range": 0.0,
            "high_scores_count": 0,
            "medium_scores_count": 0,
            "low_scores_count": 0,
        }

    median = float(statistics.median(scores))
    if len(scores) >= 4:
        quartiles = statistics.quantiles(scores, n=4)
        p25, p75 = float(quartiles[0]), float(quartiles[2])
    else:
        p25 = p75 = median

    min_score = min(scores)
    max_score = max(scores)
    return {
        "count": len(scores),
        "average_score": float(sum(scores) / len(scores)),
        "median": median,
        "p25": p25,
        "p75": p75,
        "min_score": min_score,
        "max_score": max_score,
        "score_range": max_score - min_score,
        "high_scores_count": sum(1 for s in scores if s >= 0.8),
        "medium_scores_count": sum(1 for s in scores if 0.6 <= s < 0.8),
        "low_scores_count": sum(1 for s in scores if s < 0.6),
    }


def _compute_summary(scores: List[float], elapsed: float, item_times: List[float]) -> Dict[str, Any]:
    """Summarize evaluation timing and score distribution."""
    avg_time_per_item = (sum(item_times) / len(item_times)) if item_times else 0.0
    return {
        "response_quality": _score_distribution(scores),
        "total_evaluation_time": elapsed,
        "average_time_per_item": avg_time_per_item,
    }


def _print_item_result(
    index: int,
    prompt: str,
    response: str,
    score: float,
    explanation: str,
    word_limit: int,
    duration: float,
) -> None:
    """Verbose logging for each evaluated QA item."""
    print(f"[qa_eval] Item {index + 1}")
    print(f"[qa_eval] - Word limit: {word_limit} | Eval time: {duration:.2f}s")
    print(f"[qa_eval] - Prompt: {prompt}")
    print(f"[qa_eval] - Response: {response}")
    print(f"[qa_eval] - Score: {score:.3f} | Reason: {explanation}")
    print("-" * 80)


def main() -> None:
    _configure_utf8_output()
    print(f"[qa_eval] Judge model: {EVALUATION_MODEL} (OpenRouter)")

    start = time.time()
    pairs = _load_pairs()

    results: List[Dict[str, Any]] = []
    scores: List[float] = []
    item_times: List[float] = []

    for item in pairs:
        prompt = item.get("prompt", "")
        response = item.get("response", "")
        word_limit = int(item.get("word_limit", DEFAULT_WORD_LIMIT))

        if _is_error_response(response):
            explanation = "Skipped: response contained an error."
            print(f"[qa_eval] Skipping item {len(results) + 1}: {explanation}")
            results.append(
                {
                    "prompt": prompt,
                    "score": 0.0,
                    "explanation": explanation,
                    "word_limit": word_limit,
                    "evaluation_time_seconds": 0.0,
                    "evaluation_model": EVALUATION_MODEL,
                }
            )
            _print_item_result(
                index=len(results) - 1,
                prompt=prompt,
                response=response,
                score=0.0,
                explanation=explanation,
                word_limit=word_limit,
                duration=0.0,
            )
            continue

        eval_prompt = _build_eval_prompt(prompt, response, word_limit)
        score, explanation, duration, judge_model = _evaluate_with_retries(eval_prompt)

        scores.append(score)
        item_times.append(duration)
        results.append(
            {
                "prompt": prompt,
                "score": score,
                "explanation": explanation,
                "word_limit": word_limit,
                "evaluation_time_seconds": round(duration, 2),
                "evaluation_model": judge_model or EVALUATION_MODEL,
            }
        )
        _print_item_result(
            index=len(results) - 1,
            prompt=prompt,
            response=response,
            score=score,
            explanation=explanation,
            word_limit=word_limit,
            duration=duration,
        )

    elapsed = time.time() - start
    output = {
        "results": results,
        "summary": _compute_summary(scores, elapsed, item_times),
        # Record the exact evaluation model used so reports can surface it.
        "evaluation_model": EVALUATION_MODEL,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    avg_score = output["summary"]["response_quality"]["average_score"] if scores else 0.0
    print(f"[qa_eval] Evaluated {len(results)} responses in {elapsed:.2f}s")
    print(f"[qa_eval] Average score: {avg_score:.3f}")
    print(f"[qa_eval] Output written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
