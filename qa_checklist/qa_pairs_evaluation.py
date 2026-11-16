"""
Evaluate checklist QA pairs in qa_checklist/qa_pairs.json.

Judges each pair on:
1. Answer quality — parsed answer vs allowed options (PDF-grounded generation)
2. Explanation quality — LLM explanation and citations as stated

Combined score = average of answer_score and explanation_score.
Judging uses OpenRouter via qa_checklist/llm_client.py (judges do not receive the PDF).
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import time
import warnings
from typing import Any, Optional

warnings.filterwarnings("ignore")
logging.getLogger("streamlit").setLevel(logging.CRITICAL)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

try:
    from qa_checklist.llm_client import generate_completion
    from qa_checklist.config import QA_JUDGE_FALLBACK_MODELS, QA_JUDGE_MODEL, qa_compare_models
    from qa_checklist.qa_verbose import configure_unbuffered_stdout, verbose_block, verbose_log
except Exception:
    from llm_client import generate_completion
    from config import QA_JUDGE_FALLBACK_MODELS, QA_JUDGE_MODEL, qa_compare_models
    from qa_verbose import configure_unbuffered_stdout, verbose_block, verbose_log

_judge_override = (os.environ.get("QA_JUDGE_MODEL_OVERRIDE") or "").strip()
if _judge_override:
    QA_JUDGE_MODEL = _judge_override

PAIRS_JSON = os.path.join(CURRENT_DIR, "qa_pairs.json")
EVAL_JSON = os.path.join(CURRENT_DIR, "qa_pairs_evaluation.json")


def _resume_enabled() -> bool:
    return os.environ.get("QA_RESUME", "").strip().lower() in ("1", "true", "yes")


def _pair_key(prompt_number: Any, model: str) -> tuple[Any, str]:
    return (prompt_number, str(model or ""))


def _load_existing_eval_by_key() -> dict[tuple[Any, str], dict[str, Any]]:
    if not os.path.exists(EVAL_JSON):
        return {}
    try:
        with open(EVAL_JSON, encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return {}
        by_key: dict[tuple[Any, str], dict[str, Any]] = {}
        for row in rows:
            if isinstance(row, dict):
                by_key[_pair_key(row.get("prompt_number"), row.get("model", ""))] = row
        return by_key
    except Exception:
        return {}


ANSWER_EVAL_INSTRUCTIONS = (
    "You are a STRICT evaluator of a Spec Checklist ANSWER only (not the explanation). "
    "The answer was generated from an attached specification PDF you do NOT have. "
    "Judge whether the parsed answer is correct for the EXACT checklist attribute named in "
    "the prompt — not for loosely related electrical topics. "
    "Return strict JSON: keys 'score' (0.0-1.0) and 'explanation' (brief, <= 2 sentences). "
    "0.95-1.00 = correct verbatim option or value explicitly stated FOR that attribute; "
    "'Not specified' when the explanation indicates the spec PDF is silent on that specific "
    "attribute (even if it mentions related raceway, conductor, fill, derating, or training rules). "
    "0.75-0.90 = mostly correct with minor formatting issues. "
    "0.55-0.70 = plausible but weakly supported or wrong option spelling. "
    "0.30-0.50 = wrong option despite explanation supporting another allowed value for that attribute. "
    "0.00-0.25 = empty, error, or invented value. "
    "Field-specific rules (do not conflate): "
    "circuits per raceway ≠ current-carrying conductors or conductor bundling; "
    "conduit fill / derating ≠ max circuits allowed per raceway; "
    "training for one equipment type ≠ duration for a general checklist row; "
    "a spec section cited in explanation but not in allowed answers → 'Not specified' is correct. "
    "Do NOT score 'Not specified' as wrong because the explanation mentions nine conductors, "
    "home-run combining, or NFPA fill unless it explicitly answers the named attribute. "
    "Deductions: -0.30 if not an allowed option when options listed; "
    "-0.25 only if the explanation clearly states the named attribute and the answer contradicts it. "
    "Do not judge the LLM explanation text here — answer field only."
)

EXPLANATION_EVAL_INSTRUCTIONS = (
    "You are a STRICT evaluator of a Spec Checklist LLM EXPLANATION sentence only. "
    "The explanation was generated from an attached specification PDF you do NOT have. "
    "Judge the explanation as stated — whether it plausibly supports the answer with "
    "Section/page citations when the answer is not 'Not specified'. "
    "Return strict JSON: 'score' (0.0-1.0) and 'explanation' (brief judge rationale, <= 2 sentences). "
    "Expect ONE short sentence, max ~30 words, plain language, no filler. "
    "0.95-1.00 = accurate, cites Section and page when answer is non-trivial, matches the answer. "
    "0.75-0.90 = mostly accurate with minor vagueness or slightly long. "
    "0.55-0.70 = partially supported, missing Section/page reference when answer is specific, or generic. "
    "0.30-0.50 = contradicts answer, allowed options, or cites implausible sections. "
    "0.00-0.25 = missing, error text, or completely ungrounded. "
    "Deductions: -0.30 if explanation contradicts the stated answer; "
    "-0.20 if missing when answer is non-trivial and no Section/page cite is given; "
    "-0.15 if lists allowed options or says answer 'matches' options; "
    "-0.10 if over 30 words or multi-sentence. "
    "Do not re-score the answer value itself — explanation quality only."
)


_NOT_SPECIFIED_ANSWERS = frozenset({"not specified", "n/a", "na", "none", ""})


def _checklist_field_from_prompt(prompt: str) -> str:
    """Extract the checklist row label the prompt asks about."""
    p = (prompt or "").strip()
    lower = p.lower()
    marker = "specified for "
    if marker in lower:
        idx = lower.index(marker)
        return p[idx + len(marker) :].rstrip("?").strip()
    for sep in (" for ", " regarding "):
        if sep in lower and lower.startswith("what "):
            return p.split(sep, 1)[-1].rstrip("?").strip()
    return ""


def _is_not_specified_answer(parsed_answer: str) -> bool:
    return (parsed_answer or "").strip().lower() in _NOT_SPECIFIED_ANSWERS


def _reconcile_answer_score(
    answer_score: float,
    explanation_score: float,
    parsed_answer: str,
) -> tuple[float, Optional[str]]:
    """
    When the explanation judge strongly agrees the asked field is absent,
    do not let the answer judge zero a correct 'Not specified'.
    """
    if not _is_not_specified_answer(parsed_answer):
        return answer_score, None
    if explanation_score < 0.85 or answer_score >= 0.75:
        return answer_score, None
    return (
        0.95,
        "Adjusted: explanation evaluation confirms the checklist field is absent; "
        "'Not specified' is correct (related conductor/fill limits are not the asked attribute).",
    )


def _one_line_preview(text: str, max_chars: int = 400) -> str:
    collapsed = " ".join(str(text).replace("\r\n", "\n").split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars] + "..."


def _pdf_grounding_note(item: dict[str, Any]) -> str:
    ctx = item.get("retrieved_context") or ""
    if ctx.strip():
        return f"Retrieved Context:\n{ctx}\n\n"
    pdf = item.get("context_pdf") or ""
    pdf_line = f"Context PDF: {pdf}\n" if pdf else ""
    return (
        "Note: Generation used an attached specification PDF; judge the answer, "
        "explanation, and citations as stated below (you do not have the PDF).\n"
        f"{pdf_line}\n"
    )


def _metadata_block(item: dict[str, Any]) -> str:
    parts: list[str] = []
    citations = item.get("citations")
    if citations:
        if isinstance(citations, list):
            cite_text = ", ".join(str(c) for c in citations)
        else:
            cite_text = str(citations)
        parts.append(f"Extracted Citations:\n{cite_text}\n\n")
    field_status = item.get("field_status")
    if field_status:
        parts.append(f"Field Status: {field_status}\n")
        reason = item.get("status_reason") or ""
        if reason:
            parts.append(f"Status Reason: {reason}\n")
        parts.append("\n")
    return "".join(parts)


def _build_answer_eval_prompt(item: dict[str, Any]) -> str:
    grounding = _pdf_grounding_note(item)
    field = _checklist_field_from_prompt(str(item.get("prompt", "")))
    field_block = (
        f"Checklist attribute (score ONLY whether the answer addresses this):\n{field}\n\n"
        if field
        else ""
    )
    return (
        f"Checklist Prompt:\n{item.get('prompt', '')}\n\n"
        f"{field_block}"
        f"Allowed Answers:\n{item.get('possible_answers', '')}\n\n"
        f"{grounding}"
        f"{_metadata_block(item)}"
        f"Parsed Answer:\n{item.get('parsed_answer', '')}\n\n"
        f"Raw LLM Response:\n{item.get('response', '')}\n\n"
        f"Parse Valid: {item.get('parse_valid', '')}\n"
        f"Parse Note: {item.get('parse_note', '')}\n\n"
        f"{ANSWER_EVAL_INSTRUCTIONS}\nReturn only JSON."
    )


def _build_explanation_eval_prompt(item: dict[str, Any]) -> str:
    grounding = _pdf_grounding_note(item)
    llm_expl = item.get("llm_explanation") or ""
    return (
        f"Checklist Prompt:\n{item.get('prompt', '')}\n\n"
        f"Allowed Answers:\n{item.get('possible_answers', '')}\n\n"
        f"Stated Answer:\n{item.get('parsed_answer', '')}\n\n"
        f"{grounding}"
        f"{_metadata_block(item)}"
        f"LLM Explanation to judge:\n{llm_expl or '(none provided)'}\n\n"
        f"{EXPLANATION_EVAL_INSTRUCTIONS}\nReturn only JSON."
    )


def _safe_parse_score(content: str) -> dict[str, Any]:
    def _clamp(data: dict[str, Any]) -> dict[str, Any]:
        try:
            score = float(data.get("score", 0))
        except Exception:
            score = 0.0
        score = max(0.0, min(1.0, score))
        explanation = str(data.get("explanation", "") or "")
        return {"score": score, "explanation": explanation}

    try:
        return _clamp(json.loads(content))
    except Exception:
        pass
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            try:
                return _clamp(json.loads("\n".join(lines[1:-1])))
            except Exception:
                pass
    try:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            return _clamp(json.loads(stripped[start : end + 1]))
    except Exception:
        pass
    return {"score": 0.0, "explanation": "Failed to parse evaluator output as JSON"}


def _compute_stats(score_list: list[float]) -> dict[str, Any]:
    if not score_list:
        return {
            "average_score": 0.0,
            "count": 0,
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
    avg = float(sum(score_list) / len(score_list))
    med = float(statistics.median(score_list))
    p25 = float(statistics.quantiles(score_list, n=4)[0]) if len(score_list) >= 4 else med
    p75 = float(statistics.quantiles(score_list, n=4)[2]) if len(score_list) >= 4 else med
    mn, mx = min(score_list), max(score_list)
    return {
        "average_score": avg,
        "count": len(score_list),
        "median": med,
        "p25": p25,
        "p75": p75,
        "min_score": mn,
        "max_score": mx,
        "score_range": mx - mn,
        "high_scores_count": sum(1 for s in score_list if s >= 0.8),
        "medium_scores_count": sum(1 for s in score_list if 0.6 <= s < 0.8),
        "low_scores_count": sum(1 for s in score_list if s < 0.6),
    }


def recompute_evaluation_summary(
    results: list[dict[str, Any]],
    *,
    start_time: float | None = None,
) -> dict[str, Any]:
    """Build summary dict from a filtered results list (e.g. compare models only)."""
    combined_scores = [float(r.get("score", 0.0)) for r in results]
    answer_scores = [float(r.get("answer_score", 0.0)) for r in results]
    explanation_scores = [float(r.get("explanation_score", 0.0)) for r in results]
    return _compute_summary(
        results,
        combined_scores,
        answer_scores,
        explanation_scores,
        start_time if start_time is not None else time.time(),
    )


def _filter_pairs_for_compare_models(
    items: list[dict[str, Any]],
    models: list[str],
) -> list[dict[str, Any]]:
    allowed = set(models)
    return [i for i in items if str(i.get("model") or "") in allowed]


def _pick_best_model(by_model: dict[str, dict[str, Any]]) -> str:
    if not by_model:
        return ""
    ranked = sorted(
        by_model.items(),
        key=lambda kv: (
            -kv[1].get("average_score", 0.0),
            -kv[1].get("median", 0.0),
            kv[0],
        ),
    )
    return ranked[0][0]


def _compute_summary(
    results: list[dict[str, Any]],
    combined_scores: list[float],
    answer_scores: list[float],
    explanation_scores: list[float],
    start_time: float,
) -> dict[str, Any]:
    by_model_scores: dict[str, list[float]] = {}
    for res, score in zip(results, combined_scores):
        model = str(res.get("model") or "unknown")
        by_model_scores.setdefault(model, []).append(score)

    by_model = {model: _compute_stats(slist) for model, slist in by_model_scores.items()}
    elapsed = time.time() - start_time
    n = len(results)
    return {
        "response_quality": _compute_stats(combined_scores),
        "answer_quality": _compute_stats(answer_scores),
        "explanation_quality": _compute_stats(explanation_scores),
        "by_model": by_model,
        "best_model": _pick_best_model(by_model),
        "total_evaluation_time": elapsed,
        "average_time_per_item": (elapsed / n) if n else 0.0,
    }


def _write_output(
    results: list[dict[str, Any]],
    combined_scores: list[float],
    answer_scores: list[float],
    explanation_scores: list[float],
    start_time: float,
) -> None:
    output = {
        "results": results,
        "summary": _compute_summary(
            results, combined_scores, answer_scores, explanation_scores, start_time
        ),
    }
    summ = output["summary"]
    rq = summ["response_quality"]
    verbose_log(
        "qa_eval",
        f"Writing {EVAL_JSON} (n={len(results)}, combined_avg={rq['average_score']:.3f}, "
        f"answer_avg={summ['answer_quality']['average_score']:.3f}, "
        f"expl_avg={summ['explanation_quality']['average_score']:.3f}, "
        f"best_model={summ.get('best_model', '')})",
    )
    with open(EVAL_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def _log_pair_under_review(item: dict[str, Any]) -> None:
    verbose_log("qa_eval", f"  Parsed answer: {item.get('parsed_answer', '')!r}")
    verbose_block("qa_eval", "LLM explanation", str(item.get("llm_explanation") or "(none)"))
    verbose_block("qa_eval", "Raw response", str(item.get("response") or ""))


def main() -> None:
    configure_unbuffered_stdout()
    start_time = time.time()
    verbose_log("qa_eval", "=" * 60)
    verbose_log("qa_eval", "CHECKLIST QA EVALUATION (answer + explanation)")
    verbose_log("qa_eval", "=" * 60)
    verbose_log("qa_eval", f"Reading {PAIRS_JSON}")

    with open(PAIRS_JSON, encoding="utf-8") as f:
        items: list[dict[str, Any]] = json.load(f)

    compare_models = qa_compare_models()
    before_filter = len(items)
    items = _filter_pairs_for_compare_models(items, compare_models)
    if before_filter != len(items):
        verbose_log(
            "qa_eval",
            f"  Filtered to {len(items)} pair(s) for {len(compare_models)} compare model(s) "
            f"(dropped {before_filter - len(items)} outside compare list)",
        )
    verbose_log("qa_eval", f"Loaded {len(items)} pair(s)")
    resume = _resume_enabled()
    existing_eval = _load_existing_eval_by_key() if resume else {}
    if resume:
        allowed = set(compare_models)
        existing_eval = {
            k: v for k, v in existing_eval.items() if k[1] in allowed
        }
        verbose_log(
            "qa_eval",
            f"  Mode: RESUME ({len(existing_eval)} judged pair(s) will be reused)",
        )
    verbose_log("qa_eval", f"Judge: {QA_JUDGE_MODEL}")

    seen: set[str] = set()
    candidate_models: list[str] = []
    for m in [QA_JUDGE_MODEL, *QA_JUDGE_FALLBACK_MODELS]:
        if m and m not in seen:
            seen.add(m)
            candidate_models.append(m)

    def _eval_with_retries(eval_prompt: str, label: str) -> tuple[float, str, Optional[str]]:
        system_msg = "Return only a JSON object with keys score and explanation."
        last_err = None
        for model in candidate_models:
            delay = 1.0
            for _attempt in range(3):
                try:
                    verbose_log("qa_eval", f"  Judge ({label}) via {model}...")
                    content = generate_completion(system_msg, eval_prompt, model=model)
                    parsed = _safe_parse_score(content)
                    return parsed["score"], parsed["explanation"], model
                except Exception as e:
                    last_err = str(e)
                    if "429" in last_err.lower() or "rate limit" in last_err.lower():
                        time.sleep(delay)
                        delay = min(delay * 2, 8)
                        continue
                    if "403" in last_err.lower() or "moderation" in last_err.lower():
                        break
                    time.sleep(0.5)
        return 0.0, f"{label} evaluation failed: {last_err or 'unknown'}", None

    results: list[dict[str, Any]] = []
    combined_scores: list[float] = []
    answer_scores: list[float] = []
    explanation_scores: list[float] = []
    total = len(items)
    skipped = 0
    judged = 0

    for idx, item in enumerate(items, start=1):
        prompt_preview = _one_line_preview(item.get("prompt", ""), 60)
        model = item.get("model", "")
        key = _pair_key(item.get("prompt_number"), model)
        verbose_log("qa_eval", f"[{idx}/{total}] #{item.get('prompt_number', '?')} | {model} | {prompt_preview}")

        cached_entry = existing_eval.get(key)
        if cached_entry is not None:
            skipped += 1
            entry = cached_entry
            combined = float(entry.get("score", 0.0))
            ans_score = float(entry.get("answer_score", 0.0))
            expl_score = float(entry.get("explanation_score", 0.0))
            verbose_log(
                "qa_eval",
                f"  Skip judge (cached) — combined: {combined:.3f} "
                f"(answer={ans_score:.3f}, explanation={expl_score:.3f})",
            )
        else:
            judged += 1
            _log_pair_under_review(item)
            ans_score, ans_expl, ans_judge = _eval_with_retries(
                _build_answer_eval_prompt(item), "answer"
            )
            expl_score, expl_expl, expl_judge = _eval_with_retries(
                _build_explanation_eval_prompt(item), "explanation"
            )
            adj_score, adj_note = _reconcile_answer_score(
                ans_score, expl_score, str(item.get("parsed_answer", ""))
            )
            if adj_note:
                ans_score = adj_score
                ans_expl = f"{ans_expl} [{adj_note}]"
                verbose_log("qa_eval", f"  Answer score reconciled -> {ans_score:.3f}")
            combined = (ans_score + expl_score) / 2.0
            entry = {
                "prompt_number": item.get("prompt_number"),
                "category": item.get("category", ""),
                "prompt": item.get("prompt", ""),
                "possible_answers": item.get("possible_answers", ""),
                "model": model,
                "response": item.get("response", ""),
                "parsed_answer": item.get("parsed_answer", ""),
                "llm_explanation": item.get("llm_explanation", ""),
                "parse_valid": item.get("parse_valid"),
                "score": combined,
                "answer_score": ans_score,
                "explanation_score": expl_score,
                "answer_evaluation": {
                    "score": ans_score,
                    "explanation": ans_expl,
                    "judge_model": ans_judge or "",
                },
                "explanation_evaluation": {
                    "score": expl_score,
                    "explanation": expl_expl,
                    "judge_model": expl_judge or "",
                },
                "judge_model": ans_judge or expl_judge or "",
                "explanation": f"Answer: {ans_expl} | Explanation: {expl_expl}",
            }
            verbose_log(
                "qa_eval",
                f"  Combined: {combined:.3f} (answer={ans_score:.3f}, explanation={expl_score:.3f})",
            )
            verbose_log("qa_eval", f"  Answer judge: {ans_expl}")
            verbose_log("qa_eval", f"  Explanation judge: {expl_expl}")

        results.append(entry)
        combined_scores.append(combined)
        answer_scores.append(ans_score)
        explanation_scores.append(expl_score)
        _write_output(results, combined_scores, answer_scores, explanation_scores, start_time)

    summary = _compute_summary(results, combined_scores, answer_scores, explanation_scores, start_time)
    verbose_log("qa_eval", "")
    verbose_log("qa_eval", f"  Judged this run: {judged}, reused (resume): {skipped}")
    verbose_log("qa_eval", "")
    verbose_log("qa_eval", "=" * 60)
    verbose_log("qa_eval", "EVALUATION SUMMARY")
    rq = summary["response_quality"]
    aq = summary["answer_quality"]
    eq = summary["explanation_quality"]
    verbose_log("qa_eval", f"  Combined avg: {rq['average_score']:.3f} (n={rq['count']})")
    verbose_log("qa_eval", f"  Answer avg:   {aq['average_score']:.3f}")
    verbose_log("qa_eval", f"  Explanation avg: {eq['average_score']:.3f}")
    verbose_log("qa_eval", f"  Best model: {summary['best_model']}")
    for m, stats in sorted(summary["by_model"].items()):
        verbose_log("qa_eval", f"  {m}: avg={stats['average_score']:.3f} median={stats['median']:.3f}")
    verbose_log("qa_eval", "=" * 60)


if __name__ == "__main__":
    main()
