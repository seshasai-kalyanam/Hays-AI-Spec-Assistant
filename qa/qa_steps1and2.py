#!/usr/bin/env python3
"""
Master script to run the complete QA pipeline:
1. Generate QA pairs (qa_pairs.py)
2. Evaluate QA pairs (qa_pairs_evaluation.py)
3. Generate QA report (write_qa_report.py)
"""

import os
import subprocess
import sys
from typing import List, Optional


def run_script(script_path: str, description: str) -> bool:
    """
    Run a Python script and return success status.

    Args:
        script_path: Path to the Python script
        description: Description for logging

    Returns:
        bool: True if script ran successfully, False otherwise
    """
    print(f"[qa] Starting: {description}")
    print(f"[qa] Running: python {script_path}")

    try:
        # Run from project root directory
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=False,  # Let output show in real-time
            text=True,
            timeout=None
        )

        if result.returncode == 0:
            print(f"[qa] Completed: {description}")
            return True
        else:
            print(f"[qa] Failed: {description} (exit code: {result.returncode})")
            return False

    except subprocess.TimeoutExpired:
        print(f"[qa] Timeout: {description}")
        return False
    except Exception as e:
        print(f"[qa] Error running {description}: {e}")
        return False


def check_and_delete_existing_files() -> None:
    """
    Check for existing QA JSON files and ask user if they want to delete them.
    Defaults to 'Y' (yes) if no input provided.
    """
    json_files = ["qa/qa_pairs.json", "qa/qa_pairs_evaluation.json"]
    existing_files = [f for f in json_files if os.path.exists(f)]

    if not existing_files:
        print("[qa] No existing QA JSON files found")
        return

    print("[qa] Found existing QA JSON files:")
    for file in existing_files:
        print(f"[qa]   - {file}")

    # Ask user with default Y
    try:
        response = input("[qa] Delete existing files? (Y/n): ").strip().lower()
        # Default to 'y' if empty or starts with 'y'
        should_delete = response == "" or response.startswith('y')
    except (EOFError, KeyboardInterrupt):
        # Handle cases where input is not available (e.g., in automated scripts)
        print("[qa] Input not available, defaulting to delete existing files")
        should_delete = True

    if should_delete:
        print("[qa] Deleting existing files...")
        for file in existing_files:
            try:
                os.remove(file)
                print(f"[qa] Deleted: {file}")
            except Exception as e:
                print(f"[qa] Failed to delete {file}: {e}")
        print()
    else:
        print("[qa] Keeping existing files\n")


def main() -> None:
    """Run the complete QA pipeline."""
    print("[qa] Starting QA Pipeline")
    print("=" * 50)

    # Check for existing files and ask to delete them
    check_and_delete_existing_files()

    # Define scripts to run in order
    scripts = [
        ("qa/qa_pairs.py", "Generate QA pairs"),
        ("qa/qa_pairs_evaluation.py", "Evaluate QA pairs"),
        # ("qa/write_qa_report.py", "Generate QA report")
    ]

    # Run each script in sequence
    success_count = 0
    for script_path, description in scripts:
        if run_script(script_path, description):
            success_count += 1
        else:
            print(f"[qa] Pipeline stopped due to failure in: {description}")
            break

        print()  # Add spacing between script runs

    print("=" * 50)
    if success_count == len(scripts):
        print(f"[qa] Pipeline completed successfully ({success_count}/{len(scripts)} scripts)")
    else:
        print(f"[qa] Pipeline completed with failures ({success_count}/{len(scripts)} scripts successful)")
        sys.exit(1)


if __name__ == "__main__":
    main()
