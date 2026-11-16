"""Helper script to set GEMINI_API_KEY from .streamlit/secrets.toml for shell/QA use."""
import os
import sys

from utils.streamlit_secrets import get_secret


def main() -> int:
    api_key = get_secret("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key
        print("API key set successfully from secrets")
        return 0
    print("ERROR: GEMINI_API_KEY not found in .streamlit/secrets.toml")
    return 1


if __name__ == "__main__":
    sys.exit(main())
