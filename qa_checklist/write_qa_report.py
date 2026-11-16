"""Generate markdown and interactive HTML reports from checklist QA JSON outputs."""

from __future__ import annotations

import html
import json
import os
import re
import sys
from datetime import datetime
from typing import Any, Optional

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from model_pricing import (
    DEFAULT_OUTPUT as MODEL_PRICING_PATH,
    load_pricing_json,
    lookup_model_pricing,
)

try:
    from qa_checklist.config import qa_compare_models
    from qa_checklist.qa_pairs_evaluation import recompute_evaluation_summary
except Exception:
    from config import qa_compare_models
    from qa_pairs_evaluation import recompute_evaluation_summary


def read_json_file(filepath: str) -> Any:
    try:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)
    except UnicodeDecodeError:
        with open(filepath, encoding="utf-8-sig") as f:
            return json.load(f)


def fmt2(val: Any) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def score_bar(score: Any, width: int = 14) -> str:
    try:
        s = max(0.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        return ""
    filled = max(0, min(width, int(round(s * width))))
    return "█" * filled + "░" * (width - filled)


def score_with_bar(score: Any) -> str:
    bar = score_bar(score)
    return f"{fmt2(score)} `{bar}`" if bar else fmt2(score)


def para_html(text: Any) -> str:
    if text is None:
        return ""
    return html.escape(str(text)).replace("\n", "<br/>\n")


def _pair_anchor(pair_index: int) -> str:
    return f"qa-pair-{pair_index}"


def _build_model_leaderboard(summary: dict[str, Any]) -> str:
    by_model = summary.get("by_model") or {}
    best = summary.get("best_model") or ""
    if not by_model:
        return "_No per-model statistics._\n\n"

    lines = [
        "| Model | Avg | Median | Count |",
        "|-------|-----|--------|-------|",
    ]
    ranked = sorted(
        by_model.items(),
        key=lambda kv: (-kv[1].get("average_score", 0), -kv[1].get("median", 0), kv[0]),
    )
    for model, stats in ranked:
        marker = " **best**" if model == best else ""
        lines.append(
            f"| `{model}`{marker} | {fmt2(stats.get('average_score'))} | "
            f"{fmt2(stats.get('median'))} | {stats.get('count', 0)} |"
        )
    lines.append("")
    if best:
        lines.append(f"**Best model (highest average):** `{best}`\n\n")
    return "\n".join(lines)


def _build_prompt_matrix(qa_pairs: list[dict], results: list[dict]) -> str:
    eval_by_key: dict[tuple[Any, str], dict] = {}
    for r in results:
        key = (r.get("prompt_number"), r.get("model", ""))
        eval_by_key[key] = r

    prompt_numbers = sorted({p.get("prompt_number") for p in qa_pairs if p.get("prompt_number") is not None})
    models = sorted({p.get("model", "") for p in qa_pairs if p.get("model")})

    if not prompt_numbers or not models:
        return ""

    header = "| Prompt # | " + " | ".join(f"`{m}`" for m in models) + " |"
    sep = "|----------|" + "|".join("------" for _ in models) + "|"
    rows = [header, sep]

    for pn in prompt_numbers:
        cells = [str(pn)]
        scores_for_prompt = []
        for model in models:
            ev = eval_by_key.get((pn, model), {})
            sc = ev.get("score")
            if sc is not None:
                cells.append(score_with_bar(sc))
                scores_for_prompt.append((model, float(sc)))
            else:
                cells.append("—")
        rows.append("| " + " | ".join(cells) + " |")

    return "\n".join(rows) + "\n\n"


def _pair_cell_html(pair: dict) -> str:
    parts = [
        "<strong>Raw response</strong><br/>",
        para_html(pair.get("response", "")),
        "<br/><br/><strong>Parsed answer</strong><br/>",
        para_html(pair.get("parsed_answer", "")),
        "<br/><br/><strong>LLM explanation</strong><br/>",
        para_html(pair.get("llm_explanation", "")),
        "<br/><br/><strong>Model</strong><br/>",
        para_html(pair.get("model", "")),
        "<br/><br/><strong>Possible answers</strong><br/>",
        para_html(pair.get("possible_answers", "")),
    ]
    citations = pair.get("citations")
    if citations:
        cite_text = ", ".join(citations) if isinstance(citations, list) else str(citations)
        parts.extend([
            "<br/><br/><strong>Citations</strong><br/>",
            para_html(cite_text),
        ])
    field_status = pair.get("field_status")
    if field_status:
        parts.extend([
            "<br/><br/><strong>Field status</strong><br/>",
            para_html(str(field_status)),
        ])
        reason = pair.get("status_reason")
        if reason:
            parts.extend([
                "<br/><br/><strong>Status reason</strong><br/>",
                para_html(str(reason)),
            ])
    context_pdf = pair.get("context_pdf")
    if context_pdf:
        parts.extend([
            "<br/><br/><strong>Context PDF</strong><br/>",
            para_html(str(context_pdf)),
        ])
    return "".join(parts)


def _eval_cell_html(eval_item: dict) -> str:
    ans_ev = eval_item.get("answer_evaluation") or {}
    expl_ev = eval_item.get("explanation_evaluation") or {}
    parts = [
        "<strong>Combined score</strong><br/>",
        score_with_bar(eval_item.get("score")),
        "<br/><br/><strong>Answer score</strong><br/>",
        score_with_bar(eval_item.get("answer_score", ans_ev.get("score"))),
        "<br/><br/><strong>Explanation score</strong><br/>",
        score_with_bar(eval_item.get("explanation_score", expl_ev.get("score"))),
        "<br/><br/>",
    ]
    if eval_item.get("judge_model"):
        parts.extend([
            "<strong>Judge model</strong><br/>",
            para_html(eval_item["judge_model"]),
            "<br/><br/>",
        ])
    parts.extend([
        "<strong>Answer evaluation</strong><br/>",
        para_html(ans_ev.get("explanation", "")),
        "<br/><br/><strong>Explanation evaluation</strong><br/>",
        para_html(expl_ev.get("explanation", "")),
        "<br/><br/><strong>LLM explanation (judged)</strong><br/>",
        para_html(eval_item.get("llm_explanation", "")),
    ])
    return "".join(parts)


def _load_model_pricing(script_dir: str) -> Optional[dict[str, Any]]:
    path = os.path.join(script_dir, os.path.basename(MODEL_PRICING_PATH))
    if not os.path.isfile(path):
        return None
    try:
        return load_pricing_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def _quality_vs_cost_rows(
    by_model: dict[str, Any],
    pricing_cache: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for model, stats in by_model.items():
        avg = stats.get("average_score")
        price = lookup_model_pricing(model, pricing_cache) if pricing_cache else None
        blended = (price or {}).get("blended_per_million_usd") if price else None
        rows.append({
            "model": model,
            "display_name": (price or {}).get("name") or model,
            "average_score": float(avg) if avg is not None else None,
            "blended_per_million_usd": blended,
            "prompt_per_million_usd": (price or {}).get("prompt_per_million_usd"),
            "completion_per_million_usd": (price or {}).get("completion_per_million_usd"),
        })
    return [r for r in rows if r.get("average_score") is not None]


def _build_quality_vs_cost_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No per-model quality scores._\n\n"
    lines = [
        "| Model | Avg quality | Blended $/1M tokens | Prompt $/1M | Completion $/1M |",
        "|-------|-------------|---------------------|-------------|-------------------|",
    ]
    for r in sorted(rows, key=lambda x: (-x["average_score"], x["model"])):
        cost = r.get("blended_per_million_usd")
        prompt = r.get("prompt_per_million_usd")
        completion = r.get("completion_per_million_usd")
        lines.append(
            f"| `{r['model']}` | {fmt2(r['average_score'])} | "
            f"{_fmt_usd(cost)} | {_fmt_usd(prompt)} | {_fmt_usd(completion)} |"
        )
    return "\n".join(lines) + "\n"


def _fmt_usd(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v == 0:
        return "$0"
    if v < 0.01:
        return f"${v:.4f}"
    if v < 1:
        return f"${v:.3f}"
    return f"${v:.2f}"


def _quality_vs_cost_plotted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        r for r in rows
        if r.get("blended_per_million_usd") is not None and r.get("average_score") is not None
    ]


def _plotly_div(fig, div_id: str, *, include_plotlyjs: bool = False) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs="cdn" if include_plotlyjs else False,
        config={"responsive": True},
        div_id=div_id,
    )


def _build_quality_vs_cost_figure(plotted: list[dict[str, Any]]):
    import plotly.graph_objects as go

    x = [r["blended_per_million_usd"] for r in plotted]
    y = [r["average_score"] for r in plotted]
    labels = [r["model"].split("/")[-1] for r in plotted]
    hover = [
        (
            f"<b>{r['display_name']}</b><br>"
            f"Model: {r['model']}<br>"
            f"Quality: {r['average_score']:.3f}<br>"
            f"Blended: {_fmt_usd(r['blended_per_million_usd'])}/1M tokens"
        )
        for r in plotted
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=y,
            mode="markers+text",
            text=labels,
            textposition="top center",
            textfont=dict(size=10),
            marker=dict(size=14, color="#1d4ed8", line=dict(width=0.5, color="white")),
            hovertext=hover,
            hoverinfo="text",
        )
    )
    y_min = min(y)
    y_pad = max(0.02, (max(y) - y_min) * 0.35)
    fig.update_layout(
        title="Quality vs cost (OpenRouter blended $/1M tokens)",
        xaxis_title="Blended price (USD per 1M tokens)",
        yaxis_title="Average QA quality score",
        yaxis=dict(
            range=[max(0, y_min - y_pad), min(1.05, max(y) + y_pad)],
            gridcolor="rgba(0,0,0,0.06)",
        ),
        xaxis=dict(gridcolor="rgba(0,0,0,0.06)", rangemode="tozero"),
        font=dict(family="Inter, Segoe UI, Helvetica, sans-serif", size=12),
        plot_bgcolor="#fafafa",
        paper_bgcolor="white",
        height=520,
        width=960,
        margin=dict(l=64, r=32, t=72, b=64),
        showlegend=False,
    )
    return fig


def _build_model_leaderboard_figure(summary: dict[str, Any]):
    import plotly.graph_objects as go

    by_model = summary.get("by_model") or {}
    best = summary.get("best_model") or ""
    ranked = sorted(
        by_model.items(),
        key=lambda kv: (-kv[1].get("average_score", 0), kv[0]),
    )
    if not ranked:
        return None

    models = [m.split("/")[-1] for m, _ in ranked]
    scores = [float(s.get("average_score", 0)) for _, s in ranked]
    colors = ["#15803d" if m == best else "#1d4ed8" for m, _ in ranked]
    hover = [
        f"<b>{m}</b><br>Avg: {scores[i]:.3f}<br>Median: {fmt2(s.get('median'))}<br>Count: {s.get('count', 0)}"
        for i, (m, s) in enumerate(ranked)
    ]

    fig = go.Figure(
        go.Bar(
            x=scores,
            y=models,
            orientation="h",
            marker_color=colors,
            hovertext=hover,
            hoverinfo="text",
            text=[f"{v:.2f}" for v in scores],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="Model leaderboard (average combined score)",
        xaxis_title="Average score",
        xaxis=dict(range=[0, 1.05], gridcolor="rgba(0,0,0,0.06)"),
        yaxis=dict(autorange="reversed"),
        height=max(320, 44 * len(models)),
        margin=dict(l=120, r=48, t=56, b=48),
        plot_bgcolor="#fafafa",
        paper_bgcolor="white",
    )
    return fig


def _build_prompt_heatmap_figure(qa_pairs: list[dict], results: list[dict]):
    import plotly.graph_objects as go

    eval_by_key = {(r.get("prompt_number"), r.get("model", "")): r for r in results}
    prompt_numbers = sorted(
        {p.get("prompt_number") for p in qa_pairs if p.get("prompt_number") is not None}
    )
    models = sorted({p.get("model", "") for p in qa_pairs if p.get("model")})
    if not prompt_numbers or not models:
        return None

    z: list[list[Optional[float]]] = []
    text: list[list[str]] = []
    for pn in prompt_numbers:
        row: list[Optional[float]] = []
        row_text: list[str] = []
        for model in models:
            ev = eval_by_key.get((pn, model), {})
            sc = ev.get("score")
            if sc is None:
                row.append(None)
                row_text.append("")
            else:
                row.append(float(sc))
                row_text.append(f"{float(sc):.2f}")
        z.append(row)
        text.append(row_text)

    y_labels = [f"Prompt {pn}" for pn in prompt_numbers]
    x_labels = [m.split("/")[-1] for m in models]

    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=x_labels,
            y=y_labels,
            text=text,
            texttemplate="%{text}",
            colorscale=[
                [0.0, "#b91c1c"],
                [0.5, "#fbbf24"],
                [1.0, "#15803d"],
            ],
            zmin=0,
            zmax=1,
            colorbar=dict(title="Score"),
            hoverongaps=False,
            hovertemplate="Prompt %{y}<br>Model %{x}<br>Score %{z:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Scores by prompt and model",
        xaxis_title="Model",
        yaxis_title="Prompt",
        height=max(280, 56 * len(prompt_numbers)),
        margin=dict(l=100, r=48, t=56, b=120),
        xaxis=dict(tickangle=-45),
    )
    return fig


def _build_summary_metrics_figure(summary: dict[str, Any]):
    import plotly.graph_objects as go

    rq = summary.get("response_quality", {})
    aq = summary.get("answer_quality", {})
    eq = summary.get("explanation_quality", {})
    labels = ["Combined", "Answer", "Explanation"]
    values = [
        rq.get("average_score"),
        aq.get("average_score"),
        eq.get("average_score"),
    ]
    if all(v is None for v in values):
        return None

    vals = [float(v) if v is not None else 0.0 for v in values]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=vals,
            marker_color=["#1d4ed8", "#0f766e", "#7c3aed"],
            text=[f"{v:.2f}" for v in vals],
            textposition="outside",
        )
    )
    fig.update_layout(
        title="Overall quality metrics",
        yaxis_title="Average score",
        yaxis=dict(range=[0, 1.05], gridcolor="rgba(0,0,0,0.06)"),
        height=360,
        margin=dict(l=48, r=32, t=56, b=48),
        plot_bgcolor="#fafafa",
        paper_bgcolor="white",
    )
    return fig


def _quality_vs_cost_context(
    summary: dict[str, Any],
    script_dir: str,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], str]:
    by_model = summary.get("by_model") or {}
    pricing_cache = _load_model_pricing(script_dir)
    rows = _quality_vs_cost_rows(by_model, pricing_cache)
    note = ""
    if pricing_cache is None:
        note = (
            "Pricing cache not found. Run "
            "<code>python qa_checklist/model_pricing.py</code> to refresh "
            "<code>qa_checklist/model_pricing.json</code>."
        )
    else:
        fetched = pricing_cache.get("fetched_at", "unknown")
        note = (
            f"OpenRouter public rates from <code>model_pricing.json</code> "
            f"(fetched {html.escape(str(fetched))}). "
            "Blended cost = average of prompt and completion $/1M tokens."
        )
    return pricing_cache, rows, note


def _rows_to_html_table(headers: list[str], body_rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    rows_html = []
    for row in body_rows:
        cells = "".join(f"<td>{c}</td>" for c in row)
        rows_html.append(f"<tr>{cells}</tr>")
    return f"<table class='data-table'><thead><tr>{head}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>"


def _quality_vs_cost_table_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p><em>No per-model quality scores.</em></p>"
    body = []
    for r in sorted(rows, key=lambda x: (-x["average_score"], x["model"])):
        body.append([
            f"<code>{html.escape(r['model'])}</code>",
            fmt2(r["average_score"]),
            _fmt_usd(r.get("blended_per_million_usd")),
            _fmt_usd(r.get("prompt_per_million_usd")),
            _fmt_usd(r.get("completion_per_million_usd")),
        ])
    return _rows_to_html_table(
        ["Model", "Avg quality", "Blended $/1M", "Prompt $/1M", "Completion $/1M"],
        body,
    )


def _leaderboard_table_html(summary: dict[str, Any]) -> str:
    by_model = summary.get("by_model") or {}
    best = summary.get("best_model") or ""
    if not by_model:
        return "<p><em>No per-model statistics.</em></p>"
    body = []
    ranked = sorted(
        by_model.items(),
        key=lambda kv: (-kv[1].get("average_score", 0), -kv[1].get("median", 0), kv[0]),
    )
    for model, stats in ranked:
        marker = " <strong>(best)</strong>" if model == best else ""
        body.append([
            f"<code>{html.escape(model)}</code>{marker}",
            fmt2(stats.get("average_score")),
            fmt2(stats.get("median")),
            str(stats.get("count", 0)),
        ])
    return _rows_to_html_table(["Model", "Avg", "Median", "Count"], body)


def _html_report_styles() -> str:
    return """
    body { font-family: Inter, "Segoe UI", Helvetica, sans-serif; margin: 24px 32px; color: #1e293b; line-height: 1.5; }
    h1 { font-size: 1.75rem; margin-bottom: 0.25rem; }
    h2 { font-size: 1.25rem; margin-top: 2rem; border-bottom: 1px solid #e2e8f0; padding-bottom: 0.35rem; }
    h3 { font-size: 1.05rem; margin-top: 1.5rem; }
    .meta { color: #64748b; margin-bottom: 1.5rem; }
    .metrics { display: flex; flex-wrap: wrap; gap: 12px 24px; margin: 1rem 0 1.5rem; }
    .metric { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 14px; min-width: 140px; }
    .metric strong { display: block; font-size: 1.1rem; color: #0f172a; }
    .chart { margin: 1rem 0 1.5rem; }
    .data-table { border-collapse: collapse; width: 100%; font-size: 0.9rem; margin: 0.75rem 0 1.25rem; }
    .data-table th, .data-table td { border: 1px solid #e2e8f0; padding: 6px 10px; text-align: left; }
    .data-table th { background: #f1f5f9; }
    .pair-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin: 0.75rem 0 1.5rem; }
    .pair-card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 14px; background: #fafafa; font-size: 0.9rem; }
    .pair-prompt { background: #f8fafc; border-left: 3px solid #1d4ed8; padding: 8px 12px; margin-bottom: 12px; }
    hr.pair-rule { border: none; border-top: 1px solid #e2e8f0; margin: 2rem 0; }
    @media (max-width: 900px) { .pair-grid { grid-template-columns: 1fr; } body { margin: 16px; } }
    """


def _summary_metrics_html(summary: dict[str, Any]) -> str:
    rq = summary.get("response_quality", {})
    aq = summary.get("answer_quality", {})
    eq = summary.get("explanation_quality", {})
    best = summary.get("best_model") or "—"
    items = [
        ("Combined avg", fmt2(rq.get("average_score"))),
        ("Answer avg", fmt2(aq.get("average_score"))),
        ("Explanation avg", fmt2(eq.get("average_score"))),
        ("Evaluations", str(rq.get("count", 0))),
        ("Median", fmt2(rq.get("median"))),
        ("Score range", f"{fmt2(rq.get('min_score'))} – {fmt2(rq.get('max_score'))}"),
        ("Best model", f"<code>{html.escape(best)}</code>" if best != "—" else "—"),
    ]
    blocks = "".join(
        f"<div class='metric'><span>{html.escape(label)}</span><strong>{value}</strong></div>"
        for label, value in items
    )
    return f"<div class='metrics'>{blocks}</div>"


def _try_plotly_chart(build_fn, *args, div_id: str, include_js: bool = False) -> str:
    try:
        fig = build_fn(*args)
    except ImportError:
        return "<p><em>Chart unavailable: install plotly.</em></p>"
    if fig is None:
        return ""
    return f"<div class='chart'>{_plotly_div(fig, div_id, include_plotlyjs=include_js)}</div>"


def generate_html_report(
    qa_pairs: list[dict],
    qa_evaluation: dict,
    *,
    report_stem: str,
    script_dir: str,
) -> str:
    summary = qa_evaluation.get("summary", {})
    results = qa_evaluation.get("results", [])
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _, pricing_rows, pricing_note = _quality_vs_cost_context(summary, script_dir)

    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'/>",
        f"<title>Checklist QA Report — {html.escape(report_stem)}</title>",
        f"<style>{_html_report_styles()}</style></head><body>",
        "<h1>Checklist QA Multi-Model Report</h1>",
        f"<p class='meta'>Generated on {html.escape(generated)}</p>",
        "<h2>Summary</h2>",
        _summary_metrics_html(summary),
        _try_plotly_chart(_build_summary_metrics_figure, summary, div_id="chart-summary-metrics"),
        "<h2>Quality vs Pricing</h2>",
    ]
    if pricing_note:
        parts.append(f"<p>{pricing_note}</p>")
    parts.append(_quality_vs_cost_table_html(pricing_rows))
    plotted = _quality_vs_cost_plotted_rows(pricing_rows)
    if plotted:
        parts.append(
            _try_plotly_chart(
                _build_quality_vs_cost_figure,
                plotted,
                div_id="chart-quality-vs-cost",
                include_js=True,
            )
        )
    else:
        parts.append(
            "<p><em>Chart unavailable: no models with both quality scores and OpenRouter pricing.</em></p>"
        )

    parts.extend([
        "<h2>Model Leaderboard</h2>",
        _leaderboard_table_html(summary),
        _try_plotly_chart(_build_model_leaderboard_figure, summary, div_id="chart-leaderboard"),
        "<h2>Scores by Prompt and Model</h2>",
        _try_plotly_chart(_build_prompt_heatmap_figure, qa_pairs, results, div_id="chart-heatmap"),
        "<h2>QA Pairs with Evaluation</h2>",
    ])

    eval_map = {(r.get("prompt_number"), r.get("model")): r for r in results}
    for i, pair in enumerate(qa_pairs, 1):
        key = (pair.get("prompt_number"), pair.get("model"))
        eval_item = eval_map.get(key, {})
        parts.append(f"<h3 id='{_pair_anchor(i)}'>Pair {i} — Prompt #{pair.get('prompt_number')} — <code>{html.escape(str(pair.get('model', '')))}</code></h3>")
        parts.append(
            f"<div class='pair-prompt'><strong>Prompt:</strong> {para_html(pair.get('prompt', ''))}</div>"
        )
        parts.append("<div class='pair-grid'>")
        parts.append(f"<div class='pair-card'>{_pair_cell_html(pair)}</div>")
        parts.append(f"<div class='pair-card'>{_eval_cell_html(eval_item)}</div>")
        parts.append("</div><hr class='pair-rule'/>")

    parts.append("</body></html>")
    return "".join(parts)


def _build_quality_vs_cost_section_md(
    summary: dict[str, Any],
    script_dir: str,
) -> str:
    by_model = summary.get("by_model") or {}
    if not by_model:
        return ""

    pricing_cache, rows, _ = _quality_vs_cost_context(summary, script_dir)

    section = "## Quality vs Pricing\n\n"
    if pricing_cache is None:
        section += (
            "_Pricing cache not found. Run "
            "`python qa_checklist/model_pricing.py` to refresh "
            "`qa_checklist/model_pricing.json`._\n\n"
        )
    else:
        fetched = pricing_cache.get("fetched_at", "unknown")
        section += (
            f"OpenRouter public rates from `model_pricing.json` "
            f"(fetched {fetched}). "
            f"Blended cost = average of prompt and completion $/1M tokens. "
            f"See the interactive HTML report for the quality vs cost chart.\n\n"
        )

    section += _build_quality_vs_cost_table(rows)
    return section


def generate_markdown_report(
    qa_pairs: list[dict],
    qa_evaluation: dict,
    *,
    report_stem: str,
    script_dir: str,
) -> str:
    summary = qa_evaluation.get("summary", {})
    results = qa_evaluation.get("results", [])
    rq = summary.get("response_quality", {})
    html_name = f"{report_stem}.html"

    report = "# Checklist QA Multi-Model Report\n\n"
    report += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    report += (
        f"**Interactive report (charts):** [{html_name}]({html_name})\n\n"
    )

    report += "## Summary\n\n"
    report += f"**Overall average score (combined):** {fmt2(rq.get('average_score'))}\n\n"
    aq = summary.get("answer_quality", {})
    eq = summary.get("explanation_quality", {})
    report += f"**Answer average:** {fmt2(aq.get('average_score'))}\n\n"
    report += f"**Explanation average:** {fmt2(eq.get('average_score'))}\n\n"
    report += f"**Evaluations:** {rq.get('count', 0)}\n\n"
    report += f"**Median:** {fmt2(rq.get('median'))}\n\n"
    report += f"**Score range:** {fmt2(rq.get('min_score'))} – {fmt2(rq.get('max_score'))}\n\n"

    report += _build_quality_vs_cost_section_md(summary, script_dir)

    report += "## Model Leaderboard\n\n"
    report += _build_model_leaderboard(summary)

    report += "## Scores by Prompt and Model\n\n"
    report += _build_prompt_matrix(qa_pairs, results)

    report += "## QA Pairs with Evaluation\n\n"
    eval_map = {(r.get("prompt_number"), r.get("model")): r for r in results}

    for i, pair in enumerate(qa_pairs, 1):
        key = (pair.get("prompt_number"), pair.get("model"))
        eval_item = eval_map.get(key, {})
        report += f'<a id="{_pair_anchor(i)}"></a>\n\n'
        report += f"### Pair {i} — Prompt #{pair.get('prompt_number')} — `{pair.get('model', '')}`\n\n"
        report += f"**Prompt:** {pair.get('prompt', '')}\n\n"
        report += "<table><tr>\n"
        report += '<td width="50%" valign="top">\n\n'
        report += _pair_cell_html(pair)
        report += "\n</td>\n"
        report += '<td width="50%" valign="top">\n\n'
        report += _eval_cell_html(eval_item)
        report += "\n</td>\n</tr></table>\n\n"
        report += "---\n\n"

    return report


def _filter_run_for_compare_models(
    qa_pairs: list[dict],
    qa_evaluation: dict,
) -> tuple[list[dict], dict]:
    """Keep only rows for models in QA_COMPARE_MODELS; recompute summary."""
    allowed = set(qa_compare_models())
    filtered_pairs = [p for p in qa_pairs if str(p.get("model") or "") in allowed]
    results = qa_evaluation.get("results") if isinstance(qa_evaluation, dict) else None
    if not isinstance(results, list):
        return filtered_pairs, qa_evaluation
    filtered_results = [r for r in results if str(r.get("model") or "") in allowed]
    if len(filtered_results) == len(results):
        return filtered_pairs, qa_evaluation
    filtered_eval = dict(qa_evaluation)
    filtered_eval["results"] = filtered_results
    filtered_eval["summary"] = recompute_evaluation_summary(filtered_results)
    return filtered_pairs, filtered_eval


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    qa_pairs = read_json_file(os.path.join(script_dir, "qa_pairs.json"))
    qa_evaluation = read_json_file(os.path.join(script_dir, "qa_pairs_evaluation.json"))
    qa_pairs, qa_evaluation = _filter_run_for_compare_models(qa_pairs, qa_evaluation)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_stem = f"qa_report_{ts}"
    html_path = os.path.join(script_dir, f"{report_stem}.html")
    md_path = os.path.join(script_dir, f"{report_stem}.md")

    html_report = generate_html_report(
        qa_pairs,
        qa_evaluation,
        report_stem=report_stem,
        script_dir=script_dir,
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_report)

    md_report = generate_markdown_report(
        qa_pairs,
        qa_evaluation,
        report_stem=report_stem,
        script_dir=script_dir,
    )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_report)

    print(f"Interactive report: {html_path}")
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    main()
