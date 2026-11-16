#!/usr/bin/env python3
"""
Compare which user prompts were used across two QA pipeline runs.

Each run writes qa_generation.log under qa/logs/<timestamp>/. This script
extracts the "Processing question:" lines and reports overlap and differences.

Usage (from repo root):
  python qa/compare_run_prompts.py qa/logs/20260424_031631 qa/logs/20260424_032205
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path


def _load_questions_py(repo_root: Path) -> list[str]:
    """Load ``questions`` list from ``utils/questions.py`` (same as QA generation)."""
    path = repo_root / "utils" / "questions.py"
    spec = importlib.util.spec_from_file_location("questions_mod", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    q = getattr(mod, "questions", None)
    if not isinstance(q, list):
        raise ValueError("utils/questions.py must define list `questions`")
    return [str(x) for x in q]


def _resolve_truncated(prefix: str, catalog: list[str]) -> str:
    """If ``prefix`` matches exactly one catalog entry by startswith, return it."""
    p = prefix.strip()
    if not p:
        return prefix
    if p in catalog:
        return p
    matches = [c for c in catalog if c.startswith(p)]
    if len(matches) == 1:
        return matches[0]
    return prefix


def _prompts_from_qa_generation_log(log_path: Path, catalog: list[str] | None) -> list[str]:
    if not log_path.is_file():
        raise FileNotFoundError(f"Log not found: {log_path}")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    # [03:22:11] [qa_gen] [1/20] Processing question: What types of overhead...
    pat = re.compile(
        r"\[qa_gen\]\s+\[\d+/\d+\]\s+Processing question:\s*(.+?)(?:\s*\.\.\.)?\s*$",
        re.MULTILINE,
    )
    found = []
    for m in pat.finditer(text):
        q = m.group(1).strip()
        if q.endswith("..."):
            q = q[:-3].rstrip()
        if catalog is not None:
            q = _resolve_truncated(q, catalog)
        found.append(q)
    return found


def _resolve_log_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p / "qa_generation.log"
    if p.is_file() and p.name == "qa_generation.log":
        return p
    raise ValueError(f"Expected a log directory or qa_generation.log path: {arg}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "run_a",
        help="First run: qa/logs/<ts> directory or path to qa_generation.log",
    )
    ap.add_argument(
        "run_b",
        help="Second run: qa/logs/<ts> directory or path to qa_generation.log",
    )
    ap.add_argument(
        "--no-resolve",
        action="store_true",
        help="Do not expand log-truncated prompts against utils/questions.py",
    )
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    catalog: list[str] | None = None
    if not args.no_resolve:
        try:
            catalog = _load_questions_py(repo_root)
        except Exception as e:
            print(f"WARN: could not load utils/questions.py for truncation resolve ({e})")

    log_a = _resolve_log_dir(args.run_a)
    log_b = _resolve_log_dir(args.run_b)

    prompts_a = _prompts_from_qa_generation_log(log_a, catalog)
    prompts_b = _prompts_from_qa_generation_log(log_b, catalog)

    set_a = set(prompts_a)
    set_b = set(prompts_b)
    both = sorted(set_a & set_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)

    print(f"Run A: {log_a.parent}")
    print(f"  Parsed prompts: {len(prompts_a)} (unique: {len(set_a)})")
    print(f"Run B: {log_b.parent}")
    print(f"  Parsed prompts: {len(prompts_b)} (unique: {len(set_b)})")
    print()
    print(f"Same prompt text in both runs: {len(both)}")
    for q in both:
        print(f"  - {q}")
    print()
    print(f"Only in run A: {len(only_a)}")
    for q in only_a:
        print(f"  - {q}")
    print()
    print(f"Only in run B: {len(only_b)}")
    for q in only_b:
        print(f"  - {q}")


if __name__ == "__main__":
    main()
