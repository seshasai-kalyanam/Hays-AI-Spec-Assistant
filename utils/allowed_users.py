import streamlit as st
from collections.abc import Mapping


def _list_from_secrets(value):
    """Return a cleaned list from secrets, or an empty list when absent/invalid."""
    try:
        items = list(value)
    except TypeError:
        return []
    cleaned = [item.strip() for item in items if isinstance(item, str) and item.strip()]
    return cleaned


def _auth0_secrets():
    """Safely resolve nested auth0 settings from Streamlit secrets.

    Streamlit's st.secrets returns AttrDict objects, not plain dicts, so we
    check for Mapping instead of dict.
    """
    try:
        auth_section = st.secrets.get("auth", {})
        if isinstance(auth_section, Mapping):
            auth0_section = auth_section.get("auth0", {})
            if isinstance(auth0_section, Mapping):
                return auth0_section
    except Exception:
        pass
    return {}


_auth0 = _auth0_secrets()

ALLOWED_DOMAINS = _list_from_secrets(
    st.secrets.get("ALLOWED_DOMAINS", _auth0.get("ALLOWED_DOMAINS", [])),
)
ALLOWED_EMAILS = _list_from_secrets(
    st.secrets.get("ALLOWED_EMAILS", _auth0.get("ALLOWED_EMAILS", [])),
)
