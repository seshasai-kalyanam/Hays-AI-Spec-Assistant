"""Load API keys from `.streamlit/secrets.toml` without running Streamlit.

The same TOML file is used by ``streamlit run`` and ``st.secrets``. Scripts, tests,
and CLIs can call these helpers so they stay in sync with the app and honor
``STREAMLIT_SECRETS_FILE`` when set."""

from __future__ import annotations

import os
from pathlib import Path
import tomllib
from typing import Any


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def streamlit_secrets_file(repo_root: Path | None = None) -> Path:
    """Return the path to the secrets TOML (file may not exist yet).

    Resolution order matches local Streamlit usage:

    #. ``STREAMLIT_SECRETS_FILE`` — full path to the TOML file, if set.
    #. ``<repo_root>/.streamlit/secrets.toml``

    ``repo_root`` defaults to the directory containing ``app.py`` (parent of ``utils/``).
    """
    env_path = os.environ.get("STREAMLIT_SECRETS_FILE")
    if env_path:
        return Path(env_path)
    root = repo_root if repo_root is not None else _default_repo_root()
    return root / ".streamlit" / "secrets.toml"


def load_streamlit_secrets_toml(repo_root: Path | None = None) -> dict[str, Any]:
    """Parse ``secrets.toml`` and return a dict. Returns ``{}`` if the file is missing."""
    path = streamlit_secrets_file(repo_root)
    if not path.is_file():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def require_streamlit_secrets_toml(repo_root: Path | None = None) -> dict[str, Any]:
    """Load secrets and raise if the file is missing or the TOML parses to an empty mapping."""
    path = streamlit_secrets_file(repo_root)
    if not path.is_file():
        raise FileNotFoundError(
            f"Secrets file not found: {path}. "
            "Create .streamlit/secrets.toml or set STREAMLIT_SECRETS_FILE."
        )
    with path.open("rb") as f:
        data = tomllib.load(f)
    if not data:
        raise ValueError(f"Secrets file is empty or unreadable: {path}")
    return data


def get_secret(
    key: str,
    repo_root: Path | None = None,
    default: Any = None,
) -> Any:
    """Return top-level ``key`` from secrets, with optional ``default`` if missing."""
    val = load_streamlit_secrets_toml(repo_root).get(key, default)
    if isinstance(val, str):
        return val.strip()
    return val
