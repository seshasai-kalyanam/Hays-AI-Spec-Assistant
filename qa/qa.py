#!/usr/bin/env python3
"""
Master script to run the Gemini-based QA pipeline aligned with app.py:
1. Generate QA pairs against the sample specification PDF (qa_pairs.py)
2. Evaluate QA pairs with an LLM rubric (qa_pairs_evaluation.py)
3. Generate QA report (write_qa_report.py)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from qa.qa_config import add_num_prompts_argument  # noqa: E402


def parse_args() -> argparse.Namespace:
    """CLI for the QA pipeline."""
    parser = argparse.ArgumentParser(description="Run the specification QA pipeline.")
    add_num_prompts_argument(parser)
    return parser.parse_args()


def run_script(
    script_path: str,
    description: str,
    extra_args: Sequence[str] | None = None,
) -> bool:
    """
    Run a Python script and return success status.

    Args:
        script_path: Path to the Python script
        description: Description for logging
        extra_args: Optional arguments appended to the script command

    Returns:
        bool: True if script ran successfully, False otherwise
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [sys.executable, script_path]
    if extra_args:
        cmd.extend(extra_args)

    print(f"[qa] Starting: {description}")
    print(f"[qa] Running: {' '.join(cmd)}")

    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = project_root

        process = subprocess.Popen(
            cmd,
            cwd=project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if process.stdout:
            for line in process.stdout:
                print(line.rstrip(), flush=True)

        process.wait()

        if process.returncode == 0:
            print(f"[qa] [OK] Completed: {description}")
            return True
        print(f"[qa] [FAIL] Failed: {description} (exit code: {process.returncode})")
        return False

    except subprocess.TimeoutExpired:
        print(f"[qa] [TIMEOUT] Timeout: {description}")
        return False
    except Exception as e:
        print(f"[qa] [ERROR] Error running {description}: {e}")
        return False


def check_and_delete_existing_files() -> None:
    """Delete existing QA JSON artifacts before a fresh run."""
    json_files = ["qa/qa_pairs.json", "qa/qa_pairs_evaluation.json"]
    existing_files = [f for f in json_files if os.path.exists(f)]

    if not existing_files:
        print("[qa] No existing QA JSON files found")
        return

    print("[qa] Found existing QA JSON files:")
    for file in existing_files:
        print(f"[qa]   - {file}")

    print("[qa] Deleting existing files...")
    for file in existing_files:
        try:
            os.remove(file)
            print(f"[qa] [OK] Deleted: {file}")
        except Exception as e:
            print(f"[qa] [ERROR] Failed to delete {file}: {e}")
    print()


def _load_api_key() -> None:
    """Set GEMINI_API_KEY from Streamlit secrets or the environment."""
    try:
        import streamlit as st

        api_key = st.secrets.get("GEMINI_API_KEY")
        if api_key:
            os.environ["GEMINI_API_KEY"] = api_key
            print("[qa] API key loaded from Streamlit secrets")
            return
        print("[qa] ERROR: GEMINI_API_KEY not found in Streamlit secrets")
        print("[qa] Please ensure .streamlit/secrets.toml contains GEMINI_API_KEY")
        sys.exit(1)
    except Exception as e:
        print(f"[qa] ERROR: Failed to load API key from Streamlit secrets: {e}")
        print("[qa] Checking if GEMINI_API_KEY is set in environment...")
        if not os.getenv("GEMINI_API_KEY"):
            print("[qa] ERROR: GEMINI_API_KEY not found in environment either")
            sys.exit(1)
        print("[qa] Using GEMINI_API_KEY from environment")


def main() -> None:
    """Run the complete QA pipeline."""
    args = parse_args()
    num_prompts = args.num_prompts

    print("[qa] Starting QA Pipeline")
    print(f"[qa] Prompts to run: {num_prompts}")
    print("=" * 50)

    _load_api_key()
    check_and_delete_existing_files()

    pairs_args = ["--num-prompts", str(num_prompts)]
    scripts = [
        ("qa/qa_pairs.py", "Generate QA pairs", pairs_args),
        ("qa/qa_pairs_evaluation.py", "Evaluate QA pairs", None),
        ("qa/write_qa_report.py", "Generate QA report", None),
    ]

    success_count = 0
    for script_path, description, extra_args in scripts:
        if run_script(script_path, description, extra_args):
            success_count += 1
        else:
            print(f"[qa] Pipeline stopped due to failure in: {description}")
            break
        print()

    print("=" * 50)
    if success_count == len(scripts):
        print(
            f"[qa] [SUCCESS] Pipeline completed successfully "
            f"({success_count}/{len(scripts)} scripts)"
        )
    else:
        print(
            f"[qa] [FAILURE] Pipeline completed with failures "
            f"({success_count}/{len(scripts)} scripts successful)"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
