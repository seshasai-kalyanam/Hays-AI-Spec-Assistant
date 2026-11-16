"""
Visualize QA judge scores from a qa_report_*.md (and optional sidecar JSON).

Reads a markdown report, parses summary metrics and per-pair scores. When
qa_pairs.json and qa_pairs_evaluation.json exist beside the report, uses them
for full fields (raw scores, grounding, keyword raw). Otherwise parses scores
from HTML embedded in each ### QA Pair section.

Output: qa_checklist/qa_dashboard_<report_stem>_<timestamp>.html (and PNG if kaleido).
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class Summary:
    average_score: Optional[float] = None
    count: Optional[int] = None
    median: Optional[float] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    total_evaluation_time_s: Optional[float] = None
    average_time_per_item_s: Optional[float] = None
    kw_average_score: Optional[float] = None
    kw_min_score: Optional[float] = None
    kw_max_score: Optional[float] = None


@dataclass
class PairRow:
    index: int
    prompt_short: str
    model: str
    ts: Optional[datetime]


@dataclass
class EvalRow:
    index: int
    score: float
    judge_model: Optional[str]
    kw_score: float
    grounding_frac: Optional[float]
    raw_score: Optional[float] = None
    grounding_deduction: Optional[float] = None
    kw_raw_score: Optional[float] = None
    grounding_status: Optional[str] = None


@dataclass
class ReportData:
    source_path: str
    generated_on: Optional[str]
    summary: Summary
    pairs: List[PairRow] = field(default_factory=list)
    evals: List[EvalRow] = field(default_factory=list)


def _parse_float(s: str) -> Optional[float]:
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> Optional[int]:
    s = s.strip()
    try:
        return int(float(s))
    except ValueError:
        return None


def _parse_timestamp(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


_SUMMARY_PATTERNS = {
    "average_score": re.compile(r"\*\*Average Score:\*\*\s*([^\s-]+)"),
    "count": re.compile(r"\*\*Count:\*\*\s*([0-9.]+)"),
    "median": re.compile(r"\*\*Median:\*\*\s*([0-9.]+)"),
    "min_score": re.compile(r"\*\*Min Score:\*\*\s*([0-9.]+)"),
    "max_score": re.compile(r"\*\*Max Score:\*\*\s*([0-9.]+)"),
    "total_evaluation_time": re.compile(
        r"\*\*Total Evaluation Time:\*\*\s*([0-9.eE+-]+)"
    ),
    "average_time_per_item": re.compile(
        r"\*\*Average Time per Item:\*\*\s*([0-9.eE+-]+)"
    ),
}


def _parse_summary(text: str) -> Summary:
    s = Summary()
    if m := _SUMMARY_PATTERNS["average_score"].search(text):
        s.average_score = _parse_float(m.group(1))
    if m := _SUMMARY_PATTERNS["count"].search(text):
        s.count = _parse_int(m.group(1))
    if m := _SUMMARY_PATTERNS["median"].search(text):
        s.median = _parse_float(m.group(1))
    if m := _SUMMARY_PATTERNS["min_score"].search(text):
        s.min_score = _parse_float(m.group(1))
    if m := _SUMMARY_PATTERNS["max_score"].search(text):
        s.max_score = _parse_float(m.group(1))
    if m := _SUMMARY_PATTERNS["total_evaluation_time"].search(text):
        s.total_evaluation_time_s = _parse_float(m.group(1))
    if m := _SUMMARY_PATTERNS["average_time_per_item"].search(text):
        s.average_time_per_item_s = _parse_float(m.group(1))
    return s


def _merge_keyword_summary_from_json(summary: Summary, ev_obj: dict) -> None:
    kq = (ev_obj.get("summary") or {}).get("keyword_extraction_quality") or {}
    if kq.get("average_score") is not None:
        summary.kw_average_score = float(kq["average_score"])
    if kq.get("min_score") is not None:
        summary.kw_min_score = float(kq["min_score"])
    if kq.get("max_score") is not None:
        summary.kw_max_score = float(kq["max_score"])


_PAIR_BLOCK = re.compile(
    r"### QA Pair (\d+)\n\n(.*?)(?=\n### QA Pair \d+\n\n|\Z)",
    re.DOTALL,
)

_EVAL_BLOCK = re.compile(
    r"### Evaluation (\d+)\n\n(.*?)(?=\n### Evaluation \d+\n\n|\Z)",
    re.DOTALL,
)


def _short_prompt(prompt: str, max_len: int = 52) -> str:
    p = " ".join(prompt.split())
    if len(p) <= max_len:
        return p
    return p[: max_len - 1] + "…"


_RE_MD_PROMPT = re.compile(r"\*\*Prompt:\*\*\s*(.+?)\n\n<table>", re.DOTALL)
_RE_HTML_MODEL = re.compile(r"Model</strong><br/>\s*([^<\n]+)", re.I)
_RE_HTML_TS = re.compile(r"Timestamp</strong><br/>\s*([^<\n]+)", re.I)
_RE_HTML_OVERALL = re.compile(
    r"Overall score</strong><br/>\s*([0-9.]+)", re.I
)
_RE_HTML_RAW_JUDGE = re.compile(
    r"Raw judge score</strong><br/>\s*([0-9.]+)", re.I
)
_RE_HTML_GROUND_DED = re.compile(
    r"Grounding deduction</strong><br/>\s*-([0-9.]+)", re.I
)
_RE_HTML_CITATION = re.compile(
    r"Citation grounding</strong><br/>\s*(\d+)/(\d+)\s+citations\s+verified\s+\(([^)]+)\)",
    re.I,
)
_RE_HTML_KW_SCORE = re.compile(
    r"Keywords evaluation score</strong><br/>\s*([0-9.]+)", re.I
)


def _parse_pair_eval_from_markdown_chunk(
    idx: int, chunk: str
) -> Tuple[Optional[PairRow], Optional[EvalRow]]:
    pm = _RE_MD_PROMPT.search(chunk)
    prompt = (pm.group(1).strip() if pm else "").replace("\n", " ")
    mm = _RE_HTML_MODEL.search(chunk)
    model = mm.group(1).strip() if mm else ""
    tm = _RE_HTML_TS.search(chunk)
    ts = _parse_timestamp(tm.group(1)) if tm else None
    pair = PairRow(
        index=idx,
        prompt_short=_short_prompt(prompt),
        model=model,
        ts=ts,
    )

    om = _RE_HTML_OVERALL.search(chunk)
    score = _parse_float(om.group(1)) if om else None
    if score is None:
        return pair, None

    jm = re.search(r"Judge model</strong><br/>\s*([^<\n]+)", chunk, re.I)
    judge = jm.group(1).strip() if jm else None

    km = _RE_HTML_KW_SCORE.search(chunk)
    kw_score = _parse_float(km.group(1)) if km else 0.0

    raw_m = _RE_HTML_RAW_JUDGE.search(chunk)
    raw_score = _parse_float(raw_m.group(1)) if raw_m else None

    gd_m = _RE_HTML_GROUND_DED.search(chunk)
    grounding_deduction = _parse_float(gd_m.group(1)) if gd_m else None

    gfrac: Optional[float] = None
    gstatus: Optional[str] = None
    gm = _RE_HTML_CITATION.search(chunk)
    if gm:
        a, b = int(gm.group(1)), int(gm.group(2))
        gfrac = (a / b) if b else None
        gstatus = gm.group(3).strip()

    eval_row = EvalRow(
        index=idx,
        score=score,
        judge_model=judge,
        kw_score=kw_score or 0.0,
        grounding_frac=gfrac,
        raw_score=raw_score,
        grounding_deduction=grounding_deduction,
        kw_raw_score=None,
        grounding_status=gstatus,
    )
    return pair, eval_row


def _try_load_pairs_evals_from_json(report_path: str) -> Optional[Tuple[List[PairRow], List[EvalRow]]]:
    qa_dir = os.path.dirname(os.path.abspath(report_path))
    pairs_path = os.path.join(qa_dir, "qa_pairs.json")
    eval_path = os.path.join(qa_dir, "qa_pairs_evaluation.json")
    if not (os.path.isfile(pairs_path) and os.path.isfile(eval_path)):
        return None
    try:
        with open(pairs_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        with open(eval_path, "r", encoding="utf-8") as f:
            ev = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(items, list) or not isinstance(ev, dict):
        return None
    results = ev.get("results") or []
    if not items or not results or len(items) != len(results):
        return None

    pair_rows: List[PairRow] = []
    eval_rows: List[EvalRow] = []
    for i, (pair, res) in enumerate(zip(items, results), start=1):
        prompt = str(pair.get("prompt", "") or "")
        ts = _parse_timestamp(str(pair.get("timestamp", "") or ""))
        model = str(pair.get("model", "") or "")
        pair_rows.append(
            PairRow(
                index=i,
                prompt_short=_short_prompt(prompt),
                model=model,
                ts=ts,
            )
        )
        kw_ev = res.get("keywords_evaluation") or {}
        g = res.get("grounding") or {}
        total = int(g.get("total") or 0)
        grounded = int(g.get("grounded") or 0)
        gfrac = (grounded / total) if total else None
        gstatus = str(g.get("status") or "").strip() or None
        raw_s = res.get("raw_score")
        raw_f = float(raw_s) if raw_s is not None else None
        gd = res.get("grounding_deduction")
        gd_f = float(gd) if gd is not None else None
        kw_raw = kw_ev.get("raw_score")
        kw_raw_f = float(kw_raw) if kw_raw is not None else None
        score = float(res.get("score") or 0.0)
        kw_score = float(kw_ev.get("score") or 0.0)
        judge = str(res.get("judge_model") or "").strip() or None
        eval_rows.append(
            EvalRow(
                index=i,
                score=score,
                judge_model=judge,
                kw_score=kw_score,
                grounding_frac=gfrac,
                raw_score=raw_f,
                grounding_deduction=gd_f,
                kw_raw_score=kw_raw_f,
                grounding_status=gstatus,
            )
        )
    return pair_rows, eval_rows


def _enrich_evals_from_json(report_path: str, evals: List[EvalRow]) -> None:
    """Fill raw scores and grounding from qa_pairs_evaluation.json when row counts align."""
    qa_dir = os.path.dirname(os.path.abspath(report_path))
    eval_path = os.path.join(qa_dir, "qa_pairs_evaluation.json")
    if not os.path.isfile(eval_path):
        return
    try:
        with open(eval_path, "r", encoding="utf-8") as f:
            ev = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    results = ev.get("results") or []
    if len(results) != len(evals):
        return
    for i, ev_row in enumerate(evals):
        res = results[i]
        kw_ev = res.get("keywords_evaluation") or {}
        g = res.get("grounding") or {}
        total = int(g.get("total") or 0)
        grounded = int(g.get("grounded") or 0)
        gfrac = (grounded / total) if total else None
        gstatus = str(g.get("status") or "").strip() or None
        raw_s = res.get("raw_score")
        if raw_s is not None:
            ev_row.raw_score = float(raw_s)
        gd = res.get("grounding_deduction")
        if gd is not None:
            ev_row.grounding_deduction = float(gd)
        kw_raw = kw_ev.get("raw_score")
        if kw_raw is not None:
            ev_row.kw_raw_score = float(kw_raw)
        if gfrac is not None:
            ev_row.grounding_frac = gfrac
        if gstatus:
            ev_row.grounding_status = gstatus


def _parse_legacy_markdown(text: str) -> Tuple[List[PairRow], List[EvalRow]]:
    """Older reports: **Prompt** / **Response** blocks and ### Evaluation sections."""
    pairs: List[PairRow] = []
    for m in re.finditer(
        r"### QA Pair (\d+)\n\n(.*?)(?=\n### QA Pair \d+\n\n|\n## QA Pairs Evaluation)",
        text,
        re.DOTALL,
    ):
        idx = int(m.group(1))
        chunk = m.group(2)
        pm = re.search(
            r"\*\*Prompt:\*\*\s*(.+?)\n\n\*\*Response:\*\*", chunk, re.DOTALL
        )
        prompt = (pm.group(1).strip() if pm else "").replace("\n", " ")
        mm = re.search(r"\*\*Model:\*\*\s*(.+)", chunk)
        model = mm.group(1).strip() if mm else ""
        tm = re.search(r"\*\*Timestamp:\*\*\s*(.+)", chunk)
        ts = _parse_timestamp(tm.group(1)) if tm else None
        pairs.append(
            PairRow(
                index=idx,
                prompt_short=_short_prompt(prompt),
                model=model,
                ts=ts,
            )
        )

    evals: List[EvalRow] = []
    for m in _EVAL_BLOCK.finditer(text):
        idx = int(m.group(1))
        chunk = m.group(2)
        sm = re.search(r"\*\*Score:\*\*\s*([0-9.]+)", chunk)
        score = _parse_float(sm.group(1)) if sm else 0.0
        jm = re.search(r"\*\*Judge Model:\*\*\s*(.+)", chunk)
        judge = jm.group(1).strip() if jm else None
        km = re.search(r"\*\*Keywords Evaluation Score:\*\*\s*([0-9.]+)", chunk)
        kw_score = _parse_float(km.group(1)) if km else 0.0
        gm = re.search(r"\*\*Citation Grounding:\*\*\s*(\d+)/(\d+)", chunk)
        gfrac: Optional[float] = None
        if gm:
            a, b = int(gm.group(1)), int(gm.group(2))
            gfrac = (a / b) if b else None
        evals.append(
            EvalRow(
                index=idx,
                score=score or 0.0,
                judge_model=judge,
                kw_score=kw_score or 0.0,
                grounding_frac=gfrac,
            )
        )
    return pairs, evals


def parse_qa_report(path: str) -> ReportData:
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    gen_m = re.search(r"^Generated on:\s*(.+)$", text, re.MULTILINE)
    generated_on = gen_m.group(1).strip() if gen_m else None

    sum_m = re.search(r"## Summary\n\n(.*?)(?=\n## QA Pairs)", text, re.DOTALL)
    summary = _parse_summary(sum_m.group(1) if sum_m else text)

    pairs: List[PairRow] = []
    evals: List[EvalRow] = []

    for m in _PAIR_BLOCK.finditer(text):
        idx = int(m.group(1))
        chunk = m.group(2)
        pr, er = _parse_pair_eval_from_markdown_chunk(idx, chunk)
        if pr:
            pairs.append(pr)
        if er:
            evals.append(er)

    if not evals:
        pairs, evals = _parse_legacy_markdown(text)

    if not evals:
        loaded = _try_load_pairs_evals_from_json(path)
        if loaded:
            pairs, evals = loaded

    qa_dir = os.path.dirname(os.path.abspath(path))
    eval_path = os.path.join(qa_dir, "qa_pairs_evaluation.json")
    if os.path.isfile(eval_path):
        try:
            with open(eval_path, "r", encoding="utf-8") as f:
                _merge_keyword_summary_from_json(summary, json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    pairs.sort(key=lambda p: p.index)
    evals.sort(key=lambda e: e.index)
    if pairs and evals and len(pairs) == len(evals):
        _enrich_evals_from_json(path, evals)

    return ReportData(
        source_path=path,
        generated_on=generated_on,
        summary=summary,
        pairs=pairs,
        evals=evals,
    )


def _find_latest_report(qa_dir: str) -> str:
    names = [
        n
        for n in os.listdir(qa_dir)
        if n.startswith("qa_report_") and n.endswith(".md")
    ]
    if not names:
        raise FileNotFoundError(
            f"No qa_report_*.md files in {qa_dir}. Run write_qa_report.py first."
        )
    names.sort(reverse=True)
    return os.path.join(qa_dir, names[0])


def _aligned_rows(data: ReportData) -> Tuple[List[PairRow], List[EvalRow]]:
    pmap = {p.index: p for p in data.pairs}
    emap = {e.index: e for e in data.evals}
    common = sorted(set(pmap.keys()) & set(emap.keys()))
    return [pmap[i] for i in common], [emap[i] for i in common]


def _extrema_index(vals: List[float], highest: bool) -> int:
    if highest:
        return max(range(len(vals)), key=lambda i: vals[i])
    return min(range(len(vals)), key=lambda i: vals[i])


def _dt_seconds_between_pairs(pairs: List[PairRow]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    prev: Optional[datetime] = None
    for i, p in enumerate(pairs):
        if p.ts is None:
            out.append(None)
            continue
        if i == 0 or prev is None:
            out.append(None)
        else:
            out.append(max(0.0, (p.ts - prev).total_seconds()))
        prev = p.ts
    return out


def _hover_lines(
    pairs: List[PairRow],
    evals: List[EvalRow],
    x_labels: List[str],
    deltas: List[Optional[float]],
    i: int,
) -> str:
    e = evals[i]
    lines = [
        f"<b>{x_labels[i]}</b><br>{pairs[i].prompt_short}<br>",
        f"<b>Response (final)</b> {e.score:.3f}<br>",
    ]
    if e.raw_score is not None:
        lines.append(f"<b>Response (raw judge)</b> {e.raw_score:.3f}<br>")
    if e.grounding_deduction is not None and e.grounding_deduction > 0:
        lines.append(f"<b>Grounding deduction</b> −{e.grounding_deduction:.3f}<br>")
    lines.append(f"<b>Keyword score</b> {e.kw_score:.3f}<br>")
    if e.kw_raw_score is not None:
        lines.append(f"<b>Keyword raw score</b> {e.kw_raw_score:.3f}<br>")
    if e.grounding_frac is not None:
        st = f" ({e.grounding_status})" if e.grounding_status else ""
        lines.append(f"<b>Citation grounding</b> {e.grounding_frac:.0%} verified{st}<br>")
    elif e.grounding_status:
        lines.append(f"<b>Grounding</b> {e.grounding_status}<br>")
    lines.append(f"<b>Judge</b> {e.judge_model or 'n/a'}<br>")
    lines.append(f"<b>Generation model</b> {pairs[i].model or 'n/a'}<br>")
    if deltas[i] is not None:
        lines.append(f"<i>Wall-clock since prior completion: {deltas[i]:.1f}s</i>")
    return "".join(lines)


def build_figure(data: ReportData):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    pairs, evals = _aligned_rows(data)
    if not pairs:
        raise RuntimeError(
            "No QA pairs with evaluations to plot — ensure the report includes "
            "evaluations (or run with qa_pairs.json + qa_pairs_evaluation.json present)."
        )

    x_labels = [f"Q{p.index}" for p in pairs]
    resp = [e.score for e in evals]
    kw = [e.kw_score for e in evals]
    raw_resp = [e.raw_score for e in evals]
    has_raw = any(r is not None for r in raw_resp)
    deltas = _dt_seconds_between_pairs(pairs)
    hover = [
        _hover_lines(pairs, evals, x_labels, deltas, i) for i in range(len(pairs))
    ]

    ir_lo = _extrema_index(resp, highest=False)
    ir_hi = _extrema_index(resp, highest=True)
    ik_lo = _extrema_index(kw, highest=False)
    ik_hi = _extrema_index(kw, highest=True)

    y_ext = [
        f"Response — lowest ({x_labels[ir_lo]})",
        f"Response — highest ({x_labels[ir_hi]})",
        f"Keywords — lowest ({x_labels[ik_lo]})",
        f"Keywords — highest ({x_labels[ik_hi]})",
    ]
    x_ext = [resp[ir_lo], resp[ir_hi], kw[ik_lo], kw[ik_hi]]
    ext_colors = ["#b91c1c", "#15803d", "#b91c1c", "#15803d"]
    ext_hover = [
        f"<b>{y_ext[j]}</b><br>Score: {x_ext[j]:.3f}<br>{pairs[[ir_lo, ir_hi, ik_lo, ik_hi][j]].prompt_short}<extra></extra>"
        for j in range(4)
    ]

    mean_r = sum(resp) / len(resp) if resp else 0.0
    mean_k = sum(kw) / len(kw) if kw else 0.0

    resp_colors = []
    for i in range(len(resp)):
        c = "#1d4ed8"
        if resp[i] == min(resp) and ir_lo != ir_hi:
            c = "#b91c1c"
        elif resp[i] == max(resp) and ir_lo != ir_hi:
            c = "#15803d"
        resp_colors.append(c)

    kw_colors = []
    for i in range(len(kw)):
        c = "#7c3aed"
        if kw[i] == min(kw) and ik_lo != ik_hi:
            c = "#b91c1c"
        elif kw[i] == max(kw) and ik_lo != ik_hi:
            c = "#15803d"
        kw_colors.append(c)

    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=(
            "Best and worst by metric",
            "Scores by prompt",
            "Response vs keyword quality",
            "Score distributions",
        ),
        specs=[[{"type": "bar"}, {"type": "bar"}], [{"type": "scatter"}, {"type": "box"}]],
        vertical_spacing=0.14,
        horizontal_spacing=0.10,
    )

    fig.add_trace(
        go.Bar(
            name="Extrema",
            x=x_ext,
            y=y_ext,
            orientation="h",
            marker_color=ext_colors,
            hovertext=ext_hover,
            hoverinfo="text",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Bar(
            name="Response",
            x=x_labels,
            y=resp,
            marker_color=resp_colors,
            opacity=0.92,
            text=[f"{v:.2f}" for v in resp],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            legendgroup="resp",
            showlegend=True,
        ),
        row=1,
        col=2,
    )
    fig.add_trace(
        go.Bar(
            name="Keywords",
            x=x_labels,
            y=kw,
            marker_color=kw_colors,
            opacity=0.92,
            text=[f"{v:.2f}" for v in kw],
            textposition="outside",
            hovertext=hover,
            hoverinfo="text",
            legendgroup="kw",
            showlegend=True,
        ),
        row=1,
        col=2,
    )

    fig.add_trace(
        go.Scatter(
            x=kw,
            y=resp,
            mode="markers+text",
            text=x_labels,
            textposition="top center",
            textfont=dict(size=10, color="#334155"),
            marker=dict(size=14, color="#0f766e", line=dict(width=0.5, color="white")),
            hovertext=hover,
            hoverinfo="text",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.add_hline(
        y=mean_r,
        line_dash="dot",
        line_color="rgba(30,64,175,0.45)",
        annotation_text=f"mean response {mean_r:.2f}",
        annotation_position="bottom right",
        row=2,
        col=1,
    )
    fig.add_vline(
        x=mean_k,
        line_dash="dot",
        line_color="rgba(109,40,217,0.45)",
        annotation_text=f"mean kw {mean_k:.2f}",
        annotation_position="top right",
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Box(
            y=resp,
            name="Response",
            marker_color="#1d4ed8",
            boxmean="sd",
        ),
        row=2,
        col=2,
    )
    fig.add_trace(
        go.Box(
            y=kw,
            name="Keywords",
            marker_color="#7c3aed",
            boxmean="sd",
        ),
        row=2,
        col=2,
    )
    if has_raw:
        raw_vals_clean = [r for r in raw_resp if r is not None]
        if raw_vals_clean:
            fig.add_trace(
                go.Box(
                    y=raw_vals_clean,
                    name="Response (raw)",
                    marker_color="#64748b",
                    boxmean="sd",
                ),
                row=2,
                col=2,
            )

    ann_lines = [
        f"Worst response: {x_labels[ir_lo]} {resp[ir_lo]:.3f}",
        f"Best response: {x_labels[ir_hi]} {resp[ir_hi]:.3f}",
        f"Worst keywords: {x_labels[ik_lo]} {kw[ik_lo]:.3f}",
        f"Best keywords: {x_labels[ik_hi]} {kw[ik_hi]:.3f}",
    ]
    ann_text = "<br>".join(ann_lines)

    su = data.summary
    subtitle = []
    if su.average_score is not None:
        subtitle.append(f"mean response {su.average_score:.3f}")
    if su.kw_average_score is not None:
        subtitle.append(f"mean keywords {su.kw_average_score:.3f}")
    if su.min_score is not None and su.max_score is not None:
        subtitle.append(f"response min–max {su.min_score:.3f}–{su.max_score:.3f}")
    if su.kw_min_score is not None and su.kw_max_score is not None:
        subtitle.append(f"keyword min–max {su.kw_min_score:.3f}–{su.kw_max_score:.3f}")
    timing_note = []
    if su.average_time_per_item_s is not None:
        timing_note.append(f"judge {su.average_time_per_item_s:.2f}s/item")
    if su.total_evaluation_time_s is not None:
        timing_note.append(f"total {su.total_evaluation_time_s:.0f}s")
    timing = f" ({', '.join(timing_note)})" if timing_note else ""

    judges = sorted({e.judge_model for e in data.evals if e.judge_model})
    judge_line = f"Judge: {', '.join(judges)}" if judges else ""

    fig.update_layout(
        title=dict(
            text=(
                "<b>QA run — score diagnostics</b><br>"
                f"<sup>{os.path.basename(data.source_path)}"
                + (f" · {data.generated_on}" if data.generated_on else "")
                + (f"<br>{judge_line}" if judge_line else "")
                + (f"<br>{' · '.join(subtitle)}" if subtitle else "")
                + (f"<br><i>{timing.strip(' ()')}</i>" if timing_note else "")
                + f"<br>{ann_text}"
                + "</sup>"
            ),
            x=0.02,
            xanchor="left",
        ),
        barmode="group",
        font=dict(family="Inter, Segoe UI, Helvetica, sans-serif", size=12),
        plot_bgcolor="#fafafa",
        paper_bgcolor="white",
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1,
        ),
        height=960,
        width=1220,
        margin=dict(l=56, r=100, t=140, b=72),
    )

    fig.update_xaxes(title_text="Score (0–1)", range=[0, 1.05], row=1, col=1)
    fig.update_yaxes(row=1, col=1)

    fig.update_yaxes(
        title_text="Score (0–1)",
        range=[0, 1.08],
        gridcolor="rgba(0,0,0,0.06)",
        row=1,
        col=2,
    )
    fig.update_xaxes(tickangle=-35, row=1, col=2)

    fig.update_xaxes(title_text="Keyword score", row=2, col=1)
    fig.update_yaxes(
        title_text="Response score",
        range=[0, 1.05],
        gridcolor="rgba(0,0,0,0.06)",
        row=2,
        col=1,
    )

    fig.update_yaxes(
        title_text="Score",
        range=[0, 1.05],
        gridcolor="rgba(0,0,0,0.06)",
        row=2,
        col=2,
    )

    return fig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot QA score diagnostics from a qa_report markdown file."
    )
    parser.add_argument(
        "report",
        nargs="?",
        default=None,
        help="Path to qa_report_*.md (default: latest in qa_checklist/)",
    )
    args = parser.parse_args()

    try:
        import plotly.graph_objects as go  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "plotly is required. Install with: pip install plotly"
        ) from e

    qa_dir = CURRENT_DIR
    report_path = args.report or _find_latest_report(qa_dir)
    if not os.path.isfile(report_path):
        raise FileNotFoundError(report_path)

    data = parse_qa_report(report_path)
    fig = build_figure(data)

    stem = os.path.splitext(os.path.basename(report_path))[0]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    html_path = os.path.join(qa_dir, f"qa_dashboard_{stem}_{ts}.html")
    fig.write_html(html_path, include_plotlyjs="cdn")
    print(f"Wrote interactive dashboard: {html_path}")

    png_path = os.path.join(qa_dir, f"qa_dashboard_{stem}_{ts}.png")
    try:
        fig.write_image(png_path, scale=2)
        print(f"Wrote static export: {png_path}")
    except Exception as e:
        print(
            f"(Skipped PNG — pip install kaleido for static export: {str(e)[:120]})"
        )

    pairs, evals = _aligned_rows(data)
    if pairs:
        ir_lo = _extrema_index([e.score for e in evals], highest=False)
        ir_hi = _extrema_index([e.score for e in evals], highest=True)
        ik_lo = _extrema_index([e.kw_score for e in evals], highest=False)
        ik_hi = _extrema_index([e.kw_score for e in evals], highest=True)
        print("")
        print("Score extrema:")
        print(
            f"  Response:  min Q{pairs[ir_lo].index}={evals[ir_lo].score:.3f}  "
            f"max Q{pairs[ir_hi].index}={evals[ir_hi].score:.3f}"
        )
        print(
            f"  Keywords:  min Q{pairs[ik_lo].index}={evals[ik_lo].kw_score:.3f}  "
            f"max Q{pairs[ik_hi].index}={evals[ik_hi].kw_score:.3f}"
        )
        print("")
        print("Per-item (response / keywords):")
        for i, p in enumerate(pairs):
            e = evals[i]
            print(
                f"  Q{p.index}  resp={e.score:.3f}  kw={e.kw_score:.3f}  "
                f"| {p.prompt_short[:56]}"
            )


if __name__ == "__main__":
    main()
