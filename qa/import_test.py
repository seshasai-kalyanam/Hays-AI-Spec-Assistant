"""Test imports step by step."""

import sys
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parent / "import_debug.log"

def log(message: str):
    """Log message to file."""
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{message}\n")
    print(message)

# Clear previous log
if LOG_PATH.exists():
    LOG_PATH.unlink()

log("Starting import test...")

# Add parent directory to path for imports
ROOT_DIR = Path(__file__).resolve().parent.parent
log(f"Root dir: {ROOT_DIR}")
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
    log("Added root dir to sys.path")

# Check if qa package exists
qa_init = ROOT_DIR / "qa" / "__init__.py"
if qa_init.exists():
    log("✓ qa/__init__.py exists")
else:
    log("✗ qa/__init__.py not found")
    sys.exit(1)

# Test imports
try:
    log("Importing qa.llm_client...")
    from qa.llm_client import MODEL_NAME
    log(f"✓ MODEL_NAME: {MODEL_NAME}")
except Exception as e:
    log(f"✗ Failed to import qa.llm_client: {e}")
    sys.exit(1)

try:
    log("Importing utils.system_prompt...")
    from utils.system_prompt import get_system_prompt
    log("✓ system_prompt imported")
except Exception as e:
    log(f"✗ Failed to import utils.system_prompt: {e}")
    sys.exit(1)

try:
    log("Importing utils.questions...")
    from utils.questions import questions_with_category_dictionary
    log(f"✓ questions imported, {len(questions_with_category_dictionary)} categories")
except Exception as e:
    log(f"✗ Failed to import utils.questions: {e}")
    sys.exit(1)

log("All imports successful!")


