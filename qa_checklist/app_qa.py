"""Streamlit dashboard for checklist QA multi-model evaluation results."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

SCRIPT_DIR = Path(__file__).resolve().parent


def load_json_data() -> tuple[list, dict]:
    pairs_path = SCRIPT_DIR / "qa_pairs.json"
    eval_path = SCRIPT_DIR / "qa_pairs_evaluation.json"
    try:
        with open(pairs_path, encoding="utf-8") as f:
            qa_pairs = json.load(f)
        with open(eval_path, encoding="utf-8") as f:
            evaluation_data = json.load(f)
        return qa_pairs, evaluation_data
    except FileNotFoundError as e:
        st.error(f"Error loading data: {e}")
        return [], {}


def create_model_comparison_chart(by_model: dict) -> go.Figure | None:
    if not by_model:
        return None
    models = list(by_model.keys())
    avgs = [by_model[m].get("average_score", 0) for m in models]
    fig = px.bar(
        x=models,
        y=avgs,
        title="Average Score by Model",
        labels={"x": "Model", "y": "Average Score"},
        color=avgs,
        color_continuous_scale="Blues",
    )
    fig.update_layout(showlegend=False, height=400)
    return fig


def create_prompt_model_heatmap(qa_pairs: list, results: list) -> go.Figure | None:
    eval_map = {(r.get("prompt_number"), r.get("model")): r.get("score", 0) for r in results}
    prompt_numbers = sorted({p.get("prompt_number") for p in qa_pairs if p.get("prompt_number") is not None})
    models = sorted({p.get("model") for p in qa_pairs if p.get("model")})
    if not prompt_numbers or not models:
        return None
    z = []
    for pn in prompt_numbers:
        row = [eval_map.get((pn, m), None) for m in models]
        z.append(row)
    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=models,
        y=[f"#{pn}" for pn in prompt_numbers],
        colorscale="RdYlGn",
        zmin=0,
        zmax=1,
    ))
    fig.update_layout(title="Score Heatmap (Prompt x Model)", height=400)
    return fig


def display_summary_metrics(summary: dict) -> None:
    rq = summary.get("response_quality", {})
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Overall Avg", f"{rq.get('average_score', 0):.2f}")
    with col2:
        st.metric("Evaluations", rq.get("count", 0))
    with col3:
        st.metric("Best Model", summary.get("best_model", "—"))
    with col4:
        st.metric("Median", f"{rq.get('median', 0):.2f}")


def display_pairs(qa_pairs: list, results: list) -> None:
    eval_map = {(r.get("prompt_number"), r.get("model")): r for r in results}
    st.subheader("Pairs by Prompt and Model")
    for i, pair in enumerate(qa_pairs, 1):
        key = (pair.get("prompt_number"), pair.get("model"))
        ev = eval_map.get(key, {})
        score = ev.get("score", 0)
        title = (
            f"#{pair.get('prompt_number')} | {pair.get('model', '')} | "
            f"score {score:.2f} | {pair.get('prompt', '')[:60]}"
        )
        with st.expander(title, expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Response:**")
                st.code(pair.get("response", ""))
                st.markdown(f"**Parsed:** `{pair.get('parsed_answer', '')}`")
                st.markdown(f"**LLM explanation:** {pair.get('llm_explanation', '')}")
            with c2:
                st.metric("Combined", f"{score:.2f}")
                st.metric("Answer", f"{ev.get('answer_score', 0):.2f}")
                st.metric("Explanation", f"{ev.get('explanation_score', 0):.2f}")
                ans_ev = ev.get("answer_evaluation") or {}
                expl_ev = ev.get("explanation_evaluation") or {}
                st.write("**Answer judge:**", ans_ev.get("explanation", ""))
                st.write("**Explanation judge:**", expl_ev.get("explanation", ""))


def main() -> None:
    st.set_page_config(page_title="Checklist QA Dashboard", layout="wide")
    st.title("Checklist QA Multi-Model Dashboard")

    qa_pairs, evaluation_data = load_json_data()
    if not qa_pairs or not evaluation_data:
        st.warning("Run `python qa_checklist/qa_pipeline_checklist.py` first.")
        return

    summary = evaluation_data.get("summary", {})
    results = evaluation_data.get("results", [])
    display_summary_metrics(summary)

    col1, col2 = st.columns(2)
    with col1:
        fig = create_model_comparison_chart(summary.get("by_model", {}))
        if fig:
            st.plotly_chart(fig, use_container_width=True)
    with col2:
        hm = create_prompt_model_heatmap(qa_pairs, results)
        if hm:
            st.plotly_chart(hm, use_container_width=True)

    if summary.get("by_model"):
        st.subheader("Model Leaderboard")
        rows = []
        for model, stats in summary["by_model"].items():
            rows.append({
                "Model": model,
                "Average": stats.get("average_score"),
                "Median": stats.get("median"),
                "Count": stats.get("count"),
            })
        st.dataframe(pd.DataFrame(rows).sort_values("Average", ascending=False), use_container_width=True)

    display_pairs(qa_pairs, results)


if __name__ == "__main__":
    main()
