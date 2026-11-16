#!/usr/bin/env python3
"""
Master QA Checklist Pipeline — multi-model comparison.

Runs:
1. qa_checklist/qa_generation.py
2. qa_checklist/qa_pairs_evaluation.py
3. qa_checklist/write_qa_report.py

Logs: qa_checklist/logs/<timestamp>/

Usage:
    python qa_checklist/qa_pipeline_checklist.py
    python qa_checklist/qa_pipeline_checklist.py --keep
    python qa_checklist/qa_pipeline_checklist.py --resume
    python qa_checklist/qa_pipeline_checklist.py --num-prompts 2
    python qa_checklist/qa_pipeline_checklist.py --models gemini-3.5-flash,gemini-3.1-pro-preview

Default (no flags): delete existing JSON, run all 20 curated prompts x 2 Gemini models.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except Exception:
        return False


def _subprocess_env(*, verbose: bool = True, resume: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["QA_VERBOSE"] = "1" if verbose else "0"
    if resume:
        env["QA_RESUME"] = "1"
    else:
        env.pop("QA_RESUME", None)
    return env


def _should_skip_subprocess_line(line: str) -> bool:
    return "ScriptRunContext" in line


def run_script(
    script_path: str,
    description: str,
    log_dir: str,
    *,
    verbose: bool = True,
    resume: bool = False,
) -> int:
    """Run a Python script, tee output to console and log file."""
    abs_script = os.path.join(SCRIPT_DIR, os.path.basename(script_path))
    print(f"[qa_checklist] Starting: {description}")
    print(f"[qa_checklist] Running: {sys.executable} -u {abs_script}")

    safe_name = os.path.splitext(os.path.basename(script_path))[0]
    log_path = os.path.join(log_dir, f"{safe_name}.log")

    try:
        with open(log_path, "w", encoding="utf-8") as log_file:
            log_file.write(f"=== {description} ===\n")
            log_file.write(f"=== Command: {sys.executable} -u {abs_script} ===\n")
            log_file.write(f"=== Started: {datetime.now().isoformat()} ===\n\n")
            log_file.flush()

            proc = subprocess.Popen(
                [sys.executable, "-u", abs_script],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=_subprocess_env(verbose=verbose, resume=resume),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if _should_skip_subprocess_line(line):
                    continue
                print(line, end="", flush=True)
                log_file.write(line)
                log_file.flush()
            proc.wait()
            if resume:
                log_file.write("=== QA_RESUME=1 ===\n")
            log_file.write(f"\n=== Ended: {datetime.now().isoformat()} ===\n")
            log_file.write(f"=== Exit code: {proc.returncode} ===\n")

            if proc.returncode == 0:
                print(f"[qa_checklist] SUCCESS: {description}")
            else:
                print(f"[qa_checklist] FAILED: {description} (exit {proc.returncode})")
                print(f"[qa_checklist] Log: {log_path}")
            return proc.returncode
    except Exception as e:
        print(f"[qa_checklist] ERROR: {description}: {e}")
        return 1


def check_and_delete_existing_files(auto_delete: Optional[bool] = None) -> None:
    json_files = [
        os.path.join(SCRIPT_DIR, "qa_pairs.json"),
        os.path.join(SCRIPT_DIR, "qa_pairs_evaluation.json"),
        os.path.join(SCRIPT_DIR, "qa_llm_context_cache.json"),
    ]
    existing = [f for f in json_files if os.path.exists(f)]
    if not existing:
        print("[qa_checklist] No existing JSON files")
        return

    print("[qa_checklist] Found existing JSON files:")
    for f in existing:
        print(f"[qa_checklist]   - {f}")

    if auto_delete is None:
        if not _stdin_is_tty():
            print("[qa_checklist] Non-interactive: keeping files (use --delete)")
            should_delete = False
        else:
            try:
                resp = input("[qa_checklist] Delete existing files? (Y/n): ").strip().lower()
                should_delete = resp == "" or resp.startswith("y")
            except (KeyboardInterrupt, EOFError):
                should_delete = False
    else:
        should_delete = auto_delete
        print(f"[qa_checklist] {'Deleting' if should_delete else 'Keeping'} existing files")

    if should_delete:
        for f in existing:
            try:
                os.remove(f)
                print(f"[qa_checklist] Deleted: {f}")
            except Exception as e:
                print(f"[qa_checklist] Failed to delete {f}: {e}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run checklist QA multi-model pipeline")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--delete", action="store_true",
                        help="Delete existing JSON before run (default behavior)")
    group.add_argument("--keep", action="store_true",
                        help="Keep existing qa_pairs.json, qa_pairs_evaluation.json, and qa_llm_context_cache.json")
    group.add_argument(
        "--resume",
        action="store_true",
        help="Keep existing JSON and skip prompt x model pairs already in qa_pairs.json (and cached judges)",
    )
    parser.add_argument("--num-prompts", type=int, default=None,
                        help="Rows from prompts_curated.csv (default 20 from config)")
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="Specification PDF path for generation (default: sample_pdf/RSE1A IFC EP Design - Electrical SPEC.pdf)",
    )
    parser.add_argument("--models", type=str, default=None,
                        help="Comma-separated Gemini model IDs for generation (overrides QA_COMPARE_MODELS)")
    parser.add_argument("--regression-threshold", type=float, default=None)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Reduce subprocess terminal output (sets QA_VERBOSE=0)",
    )
    return parser.parse_args()


def _apply_overrides_to_env(args: argparse.Namespace) -> None:
    if args.pdf is not None:
        os.environ["QA_CONTEXT_PDF"] = args.pdf.strip()
    if args.num_prompts is not None:
        os.environ["QA_NUM_PROMPTS"] = str(args.num_prompts)
    if args.models is not None:
        os.environ["QA_COMPARE_MODELS"] = args.models.strip()


def _git_sha_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except Exception:
        return "unknown"


def _write_manifest(log_dir: str, args: argparse.Namespace, start_iso: str, end_iso: str, results: list) -> None:
    manifest = {
        "run_started": start_iso,
        "run_ended": end_iso,
        "git_sha": _git_sha_short(),
        "args": {
            "delete": bool(args.delete),
            "keep": bool(args.keep),
            "resume": bool(args.resume),
            "num_prompts": args.num_prompts,
            "pdf": args.pdf,
            "models": args.models,
            "regression_threshold": args.regression_threshold,
        },
        "scripts": results,
    }
    with open(os.path.join(log_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def _load_eval_avg(eval_path: str) -> Optional[float]:
    try:
        with open(eval_path, encoding="utf-8") as f:
            data = json.load(f)
        return float(data.get("summary", {}).get("response_quality", {}).get("average_score"))
    except Exception:
        return None


def _previous_avg() -> Optional[float]:
    logs_dir = os.path.join(SCRIPT_DIR, "logs")
    if not os.path.isdir(logs_dir):
        return None
    for run in sorted(os.listdir(logs_dir), reverse=True):
        mpath = os.path.join(logs_dir, run, "manifest.json")
        if not os.path.exists(mpath):
            continue
        try:
            with open(mpath, encoding="utf-8") as f:
                avg = json.load(f).get("previous_avg_at_run_end")
            if isinstance(avg, (int, float)):
                return float(avg)
        except Exception:
            continue
    return None


def main() -> None:
    args = parse_args()
    print("[qa_checklist] Starting pipeline")
    print("=" * 60)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(SCRIPT_DIR, "logs", run_ts)
    os.makedirs(log_dir, exist_ok=True)
    print(f"[qa_checklist] Log dir: {log_dir}")

    _apply_overrides_to_env(args)
    if args.num_prompts is not None:
        print(f"[qa_checklist]   --num-prompts = {args.num_prompts}")
    if args.pdf is not None:
        print(f"[qa_checklist]   --pdf = {args.pdf}")
    if args.models is not None:
        print(f"[qa_checklist]   --models = {args.models}")
    if args.resume:
        print("[qa_checklist]   --resume (skip completed prompt x model pairs)")
        try:
            from qa_checklist.config import qa_compare_models

            models = qa_compare_models()
            print(f"[qa_checklist]   compare models ({len(models)}): {', '.join(models)}")
        except Exception:
            pass
    pipeline_verbose = not args.quiet
    if pipeline_verbose:
        print("[qa_checklist] Verbose LLM logging enabled (use --quiet to disable)")

    auto_delete = False if (args.keep or args.resume) else True
    check_and_delete_existing_files(auto_delete=auto_delete)

    scripts = [
        ("qa_generation.py", "Generate checklist QA pairs"),
        ("qa_pairs_evaluation.py", "Evaluate checklist QA pairs"),
        ("write_qa_report.py", "Generate QA report"),
    ]

    start_iso = datetime.now().isoformat()
    results = []
    gen_had_output = False

    for script_name, description in scripts:
        code = run_script(
            script_name,
            description,
            log_dir,
            verbose=pipeline_verbose,
            resume=bool(args.resume),
        )
        results.append({"script": script_name, "description": description, "exit_code": code})

        if script_name == "qa_generation.py":
            out_path = os.path.join(SCRIPT_DIR, "qa_pairs.json")
            try:
                with open(out_path, encoding="utf-8") as f:
                    items = json.load(f)
                gen_had_output = isinstance(items, list) and len(items) > 0
            except Exception:
                gen_had_output = False
            if code != 0 and not gen_had_output:
                print("[qa_checklist] Pipeline stopped: no generation output")
                break
        print()

    end_iso = datetime.now().isoformat()
    eval_path = os.path.join(SCRIPT_DIR, "qa_pairs_evaluation.json")
    current_avg = _load_eval_avg(eval_path)
    prior_avg = _previous_avg()

    regression_failed = False
    if current_avg is not None and prior_avg is not None and args.regression_threshold is not None:
        delta = prior_avg - current_avg
        if delta > float(args.regression_threshold):
            regression_failed = True
            print(f"[qa_checklist] REGRESSION: {prior_avg:.3f} -> {current_avg:.3f} (delta -{delta:.3f})")

    _write_manifest(log_dir, args, start_iso, end_iso, results)
    if current_avg is not None:
        try:
            mpath = os.path.join(log_dir, "manifest.json")
            with open(mpath, encoding="utf-8") as f:
                m = json.load(f)
            m["previous_avg_at_run_end"] = current_avg
            with open(mpath, "w", encoding="utf-8") as f:
                json.dump(m, f, indent=2)
        except Exception:
            pass

    ok = sum(1 for r in results if r["exit_code"] == 0)
    print("=" * 60)
    if ok == len(results) and not regression_failed:
        print(f"[qa_checklist] SUCCESS ({ok}/{len(results)} scripts)")
    elif regression_failed:
        print("[qa_checklist] FAILED: regression gate")
        sys.exit(3)
    else:
        print(f"[qa_checklist] PARTIAL ({ok}/{len(results)} scripts)")
        sys.exit(1)


if __name__ == "__main__":
    main()
