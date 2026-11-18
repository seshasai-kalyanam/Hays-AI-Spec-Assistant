"""Shared logged-out landing page for the Hays Specifications Assistant."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import streamlit as st

from utils.auth0_utils import login_button
from utils.config_vars import (
    LOGIN_PAGE_FEATURES_CHAT,
    LOGIN_PAGE_FEATURES_CHECKLIST,
    LOGIN_PAGE_INTRO,
    LOGIN_PAGE_SIGN_IN_NOTE,
)
from utils.neon_utils import save_guest_email
from utils.ui_theme import inject_gpt_inspired_layout_css

_REPO_ROOT = Path(__file__).resolve().parent.parent


@st.cache_data
def hays_logo_data_uri() -> str:
    """Return the Hays logo as a data URI for inline HTML headers."""
    path = _REPO_ROOT / "img" / "Hays_logo_lt2.png"
    b64 = base64.b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


def load_privacy_policy_text() -> str:
    """Load privacy policy text from utils/privacy_policy.txt."""
    policy_path = Path(__file__).resolve().parent / "privacy_policy.txt"
    try:
        return policy_path.read_text(encoding="utf-8").strip()
    except Exception:
        return "Privacy policy text is currently unavailable."


def render_hays_app_header() -> None:
    """Logo and title in one compact row."""
    st.markdown(
        f"""
        <div class="hays-app-header">
            <img src="{hays_logo_data_uri()}" alt="Hays" width="56" />
            <div class="hays-app-header-text">
                <h3>Hays Specifications Assistant</h3>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_landing_header() -> None:
    render_hays_app_header()


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_guest_email(value: str) -> bool:
    """True when the value looks like an email address."""
    return bool(_EMAIL_RE.match((value or "").strip()))


def render_guest_email_gate() -> bool:
    """Ask for an email until one is stored. Returns True when the app can continue."""
    current = str(st.session_state.get("guest_email") or "").strip()
    if valid_guest_email(current):
        return True

    inject_gpt_inspired_layout_css()
    render_hays_app_header()
    st.markdown("Enter your email to use the assistant. It is saved with your questions so we know who asked them.")
    with st.form("guest_email_form", border=False):
        email = st.text_input("Email", placeholder="name@hayselectrical.com")
        submitted = st.form_submit_button("Continue", type="primary")
    if submitted:
        cleaned = (email or "").strip()
        if valid_guest_email(cleaned):
            st.session_state.guest_email = cleaned
            save_guest_email(cleaned)
            st.rerun()
        st.error("Enter a valid email address.")
    return False


def render_logged_out_page(*, show_privacy: bool = True) -> None:
    """Render the sign-in landing page for unauthenticated users."""
    inject_gpt_inspired_layout_css()
    _render_landing_header()
    st.markdown(LOGIN_PAGE_INTRO)

    st.markdown("**What you can do after sign-in**")
    col_chat, col_checklist = st.columns(2)
    with col_chat:
        st.markdown("##### Specifications chat")
        st.markdown(LOGIN_PAGE_FEATURES_CHAT)
    with col_checklist:
        st.markdown("##### Spec. Compliance Review Checklist")
        st.markdown(LOGIN_PAGE_FEATURES_CHECKLIST)

    st.markdown(LOGIN_PAGE_SIGN_IN_NOTE)
    login_button("auth0", "Log in", type="primary", use_container_width=False)
    st.info("Questions? Contact **sesha.kalyanam@hayselectrical.com**")

    if show_privacy:
        with st.expander("Privacy Policy", expanded=False):
            st.markdown(load_privacy_policy_text())
