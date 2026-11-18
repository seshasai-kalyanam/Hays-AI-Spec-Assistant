"""
Reusable Auth0 authentication and authorization functions for Streamlit applications.

This module provides a comprehensive set of utility functions for handling
authentication, authorization, and user management with Auth0 in Streamlit apps.

QUICK START:
------------
1. Configure Auth0 in your .streamlit/secrets.toml.

   Shared Streamlit auth settings (required). ``redirect_uri`` is the exact URL
   Auth0 redirects to after login; Streamlit always uses this value for
   ``st.login("auth0")``, not any URI under ``[auth.auth0]``. It must match
   Auth0 Application Settings → Allowed Callback URLs (including path
   ``/oauth2callback``). On **Streamlit Community Cloud**, the live URL includes
   a ``/~/+/`` prefix, e.g.
   ``https://your-app.streamlit.app/~/+/oauth2callback``—use that exact string
   in both secrets and Auth0, not ``https://.../oauth2callback`` alone.

   [auth]
   redirect_uri = "http://localhost:8501/oauth2callback"
   cookie_secret = "at-least-32-char-random-secret"

   [auth.auth0]
   domain = "your-tenant.auth0.com"
   client_id = "your_client_id"
   client_secret = "your_client_secret"
   server_metadata_url = "https://your-tenant.auth0.com/.well-known/openid-configuration"

2. Use in your Streamlit app:
   
   from utils.auth0_utils import require_login, get_user_email, display_user_profile
   
   require_login()  # Protect a page
   email = get_user_email()
   display_user_profile()

FUNCTIONS BY CATEGORY:
---------------------
Authentication:
  - is_user_logged_in()          : Check if user is authenticated
  - require_login()               : Guard function - stops execution if not logged in
  - login_button()                : Create a login button
  - logout_button()               : Create a logout button
User Information:
  - get_user_info()               : Get all user claims as dictionary
  - get_user_email()              : Get user's email address
  - get_user_attribute()          : Get any user attribute from token
  - is_email_verified()           : Check if user's email is verified

Authorization:
  - is_domain_allowed()           : Check if user's email domain is allowed
  - is_email_allowed()            : Check if user's email is in allowed list
  - is_user_access_allowed()      : Check if user has access via domain or email
  - require_allowed_domain()      : Guard function - stops execution if domain/email not allowed
  - require_verified_email()      : Guard for verified-email claim

UI Helpers:
  - display_user_profile()        : Display user profile with picture and name
  - display_email_verification_ui(): Unverified-email gate (profile + primary log out)

USAGE EXAMPLES:
--------------
Example 1: Protected Page
    import streamlit as st
    from utils.auth0_utils import require_login, get_user_email, display_user_profile
    
    require_login()  # Stops execution if not logged in
    st.title("Welcome!")
    st.write(f"Hello, {get_user_email()}!")
    display_user_profile()

Example 2: Login Page
    import streamlit as st
    from utils.auth0_utils import is_user_logged_in, login_button, get_user_email
    
    if not is_user_logged_in():
        st.title("Please Log In")
        login_button("auth0", "Log in with Auth0", type="primary", use_container_width=True)
    else:
        st.success(f"Welcome, {get_user_email()}!")

Example 3: Email Verification Check
    import streamlit as st
    from utils.auth0_utils import require_login, is_email_verified, display_email_verification_ui
    
    require_login()
    if not is_email_verified():
        display_email_verification_ui()
        st.stop()
    st.write("Your email is verified!")

Example 4: Domain-Based Access Control
    import streamlit as st
    from utils.auth0_utils import require_login, require_allowed_domain
    
    require_login()  # Stops execution if not logged in
    require_allowed_domain()  # Stops execution if domain/email not allowed
    st.write("Welcome, authorized user!")

DEPENDENCIES:
-------------
  - streamlit (for st.user, st.login, st.logout, etc.)

Reference: https://docs.streamlit.io/develop/api-reference/user/st.login
"""

import streamlit as st
from typing import Dict, Any, Optional

# Export list for cleaner imports
__all__ = [
    # Authentication
    "is_user_logged_in",
    "require_login",
    "require_verified_email",
    "login_button",
    "logout_button",
    # User Information
    "get_user_info",
    "get_user_email",
    "get_user_attribute",
    "is_email_verified",
    # Authorization
    "is_domain_allowed",
    "is_email_allowed",
    "is_user_access_allowed",
    "require_allowed_domain",
    # UI Helpers
    "display_user_profile",
    "display_email_verification_ui",
]


# =============================================================================
# CORE AUTHENTICATION FUNCTIONS
# =============================================================================


def is_user_logged_in() -> bool:
    """
    Check if the current user is authenticated.
    
    Returns:
        bool: True if the user is logged in, False otherwise.
    
    Example:
        >>> if is_user_logged_in():
        ...     st.write("Welcome back!")
        ... else:
        ...     st.write("Please log in")
    
    Note:
        Uses defensive programming with getattr() to handle cases where
        st.user might not be available. According to Streamlit docs,
        st.user.is_logged_in is the standard property.
    """
    return getattr(st.user, "is_logged_in", False)


def get_user_info() -> Dict[str, Any]:
    """
    Extract user information as a dictionary from the current user's identity token.
    
    Returns:
        dict: Dictionary containing all user claims/attributes from the ID token.
              Returns an empty dict if the user is not logged in.
    
    Example:
        >>> user_info = get_user_info()
        >>> if user_info:
        ...     st.write(f"Email: {user_info.get('email')}")
        ...     st.write(f"Name: {user_info.get('name')}")
    
    Note:
        st.user contains identity token claims accessible as both attributes
        (e.g., st.user.name) and as a dict-like object (e.g., st.user.get("email")).
        This function handles multiple access patterns: to_dict() method,
        dict(st.user.items()), or attribute access.
    """
    if not is_user_logged_in():
        return {}
    
    if hasattr(st.user, "to_dict"):
        return st.user.to_dict()
    
    if hasattr(st.user, "items"):
        return dict(st.user.items())
    
    # Fallback: try to get common attributes
    user_info = {}
    common_attrs = ["email", "name", "nickname", "picture", "sub", "email_verified"]
    for attr in common_attrs:
        if hasattr(st.user, attr):
            user_info[attr] = getattr(st.user, attr)
    
    return user_info


def get_user_email() -> Optional[str]:
    """
    Get the current user's email address.

    A typed guest email is used while Auth0 sign-in is turned off. An Auth0
    login still wins when one is present.

    Returns:
        str or None: The user's email address if available, None otherwise.
    
    Example:
        >>> email = get_user_email()
        >>> if email:
        ...     st.write(f"Logged in as {email}")
    
    Note:
        Extracts email from user info dict. Returns None if user is not
        logged in or email is not available in the identity token.
    """
    if is_user_logged_in():
        user_info = get_user_info()
        email = user_info.get("email")
        if email:
            return str(email).strip()
    guest = str(st.session_state.get("guest_email") or "").strip()
    return guest or None


def get_user_attribute(attribute_name: str, default: Any = None) -> Any:
    """
    Get a specific user attribute from the identity token.
    
    Parameters:
        attribute_name (str): The name of the attribute to retrieve
                             (e.g., "email", "name", "picture", "sub", "email_verified").
        default (Any, optional): Default value to return if attribute doesn't exist.
                                Defaults to None.
    
    Returns:
        Any: The value of the requested attribute, or the default value if not found.
    
    Example:
        >>> user_name = get_user_attribute("name", "Unknown User")
        >>> user_picture = get_user_attribute("picture")
        >>> user_id = get_user_attribute("sub")
        >>> st.write(f"Hello, {user_name}!")
    
    Note:
        This is a generic function to retrieve any user attribute from the
        identity token claims. Useful for accessing custom claims or optional
        attributes that may not always be present.
    """
    if not is_user_logged_in():
        return default
    
    user_info = get_user_info()
    return user_info.get(attribute_name, default)


def is_email_verified() -> bool:
    """
    Check if the current user's email address is verified.
    
    Returns:
        bool: True if email is verified, False if not verified or user is not logged in.
    
    Example:
        >>> if is_email_verified():
        ...     st.write("Email verified!")
        ... else:
        ...     st.warning("Please verify your email")
    
    Note:
        Returns False when the email_verified claim is missing. This app requires
        Auth0 account verification before the legal research dashboard is shown.
    """
    if not is_user_logged_in():
        return False
    
    email_verified = get_user_attribute("email_verified", False)
    return email_verified is True


def require_verified_email() -> bool:
    """
    Require Auth0 email/account verification before protected app rendering.

    Returns:
        bool: True if the logged-in user's email is verified.

    Note:
        This guard intentionally fails closed. If Auth0 does not provide an
        email_verified=True claim, the dashboard remains unavailable.
    """
    if not is_user_logged_in():
        st.session_state["authenticated"] = False
        _prompt_login_and_stop()

    if not is_email_verified():
        st.session_state["authenticated"] = False
        display_email_verification_ui()
        st.stop()

    st.session_state["authenticated"] = True
    return True


# =============================================================================
# AUTHORIZATION FUNCTIONS
# =============================================================================

def _prompt_login_and_stop(message: Optional[str] = None) -> None:
    """Initiate the Auth0 login flow immediately and halt the script.

    Calling st.login() triggers a redirect to Auth0. If the user already has
    an active Auth0 / SSO session their browser is sent straight back without
    any manual interaction, so returning users land in the app automatically.

    We also call st.stop() right after so that any downstream guards (e.g.
    require_allowed_domain) do not briefly render access-denied UI during the
    split-second before the browser follows the Auth0 redirect.
    """
    st.login("auth0")
    st.stop()


def require_login() -> bool:
    """
    Require user to be logged in - guard function that stops execution if not authenticated.
    
    Returns:
        bool: True if user is logged in (execution continues).
    
    Example:
        >>> require_login()  # Stops execution if not logged in
        >>> st.write("This content is only visible to logged-in users")
    
    Note:
        This is a guard function that shows the sign-in UI and calls st.stop()
        if the user is not logged in. Use this at the top of pages that require
        authentication. Execution only continues if the user is authenticated.
    """
    if not is_user_logged_in():
        _prompt_login_and_stop()
    return True


def is_domain_allowed() -> bool:
    """
    Check if the current user's email domain is in the allowed list.
    
    Returns:
        bool: True if user's email domain is in ALLOWED_DOMAINS, False otherwise.
    
    Example:
        >>> if is_domain_allowed():
        ...     st.write("Domain is allowed")
        ... else:
        ...     st.write("Domain is not allowed")
    
    Note:
        Domain comparison is case-insensitive. Returns False if user is not
        logged in, email is missing, or domain is not in the allowed list.
    """
    if not is_user_logged_in():
        return False
    
    user_email = get_user_email()
    if not user_email:
        return False
    
    from utils.allowed_users import ALLOWED_DOMAINS
    
    def _normalize_domain(value: str) -> str:
        domain = value.strip().lower()
        return domain[4:] if domain.startswith("www.") else domain

    user_domain = _normalize_domain(user_email.split("@")[-1])
    allowed_domains_normalized = {_normalize_domain(domain) for domain in ALLOWED_DOMAINS if domain}
    return user_domain in allowed_domains_normalized


def is_email_allowed() -> bool:
    """
    Check if the current user's email is in the allowed emails list.
    
    Returns:
        bool: True if user's email is in ALLOWED_EMAILS, False otherwise.
    
    Example:
        >>> if is_email_allowed():
        ...     st.write("Email is allowed")
        ... else:
        ...     st.write("Email is not allowed")
    
    Note:
        Email comparison is case-insensitive. Returns False if user is not
        logged in, email is missing, or email is not in the allowed list.
    """
    if not is_user_logged_in():
        return False
    
    user_email = get_user_email()
    if not user_email:
        return False
    
    from utils.allowed_users import ALLOWED_EMAILS
    
    normalized_user_email = user_email.strip().lower()
    allowed_emails_lower = {email.strip().lower() for email in ALLOWED_EMAILS if email}
    return normalized_user_email in allowed_emails_lower


def is_user_access_allowed() -> bool:
    """
    Check if user has access via either domain or email.
    
    Returns:
        bool: True if user's email domain is in ALLOWED_DOMAINS OR user's email
              is in ALLOWED_EMAILS, False otherwise.
    
    Example:
        >>> if is_user_access_allowed():
        ...     st.write("Access granted")
        ... else:
        ...     st.write("Access denied")
    
    Note:
        User has access if their email domain matches an allowed domain OR
        their email address matches an allowed email. Returns False if user
        is not logged in or email is missing.
    """
    return is_domain_allowed() or is_email_allowed()


def require_allowed_domain() -> bool:
    """
    Require user's email domain or email to be allowed - guard function that stops execution if not allowed.
    
    Returns:
        bool: True if user's domain or email is allowed (execution continues).
    
    Example:
        >>> require_allowed_domain()  # Stops execution if domain/email not allowed
        >>> st.write("Welcome, authorized user!")
    
    Note:
        This is a guard function that checks login status first, then validates
        that the user's email domain is in ALLOWED_DOMAINS OR their email is in
        ALLOWED_EMAILS. Shows error message with contact information and calls
        st.stop() if access is denied. Execution only continues if the user is
        both logged in and has an allowed domain or email.
    """
    if not is_user_logged_in():
        _prompt_login_and_stop("You need to log in to access this application.")
    
    if not is_user_access_allowed():
        st.error("🚫 Access Denied: Your email address is not authorized to use this application.")
        st.info("If you believe you should have access, please contact [sesha.kalyanam@hayselectrical.com](mailto:sesha.kalyanam@hayselectrical.com).")
        logout_button("Log out", width="stretch")
        st.stop()
    
    return True


# =============================================================================
# UI HELPER FUNCTIONS
# =============================================================================

def display_user_profile(
    user_info: Optional[Dict[str, Any]] = None,
    *,
    sidebar_account: bool = False,
) -> None:
    """
    Display user profile information in a formatted layout.

    Parameters:
        user_info (dict, optional): User information dictionary. If not provided,
            retrieves current user info using get_user_info().
        sidebar_account: If True, places **Log out** under the avatar in the first
            column and details in the second (for the Account sidebar expander).
            If False, uses the default image | text row layout without a logout
            control.

    Example:
        >>> display_user_profile()  # Uses current user
        >>> display_user_profile(sidebar_account=True)  # Account expander layout
    """
    if user_info is None:
        user_info = get_user_info()

    if not user_info:
        st.caption("Profile information not available")
        return

    picture_url = user_info.get("picture")
    name = user_info.get("name")
    nickname = user_info.get("nickname") or user_info.get("preferred_username")
    email = user_info.get("email") or get_user_email()

    result: list[str] = []
    if name:
        result.append(f"**Account:** {name}")
    if nickname:
        result.append(f"**Nickname:** {nickname}")
    if email:
        result.append(f"**Email:** {email}")
    details_md = "  \n".join(result) if result else ""

    if sidebar_account:
        col_left, col_right = st.columns([1, 3], vertical_alignment="top")
        with col_left:
            if picture_url:
                st.image(picture_url, width=100)
            else:
                st.caption("Profile image not provided")
            logout_button("Log out", width="stretch")
        with col_right:
            if details_md:
                st.markdown(details_md)
    else:
        col1, col2 = st.columns([1, 3], vertical_alignment="center")
        with col1:
            if picture_url:
                st.image(picture_url, width=100)
            else:
                st.caption("Profile image not provided")
        with col2:
            if details_md:
                st.markdown(details_md)


def login_button(
    provider: str = "auth0",
    button_text: str = "Log in",
    **kwargs
) -> bool:
    """
    Create a login button that initiates the authentication flow.
    
    Parameters:
        provider (str, optional): The name of the provider configuration to use.
                                 Defaults to "auth0". Can be None (uses [auth] config),
                                 or a string like "auth0", "microsoft", "okta".
        button_text (str, optional): Text to display on the button. Defaults to "Log in".
        **kwargs: Additional keyword arguments passed to st.button().
                 Common options: type="primary", use_container_width=True.
    
    Returns:
        bool: True if button was clicked, False otherwise.
    
    Example:
        >>> if not is_user_logged_in():
        ...     if login_button("auth0", "Log in with Auth0", type="primary"):
        ...         pass  # st.login() handles the redirect
    
    Note:
        Wrapper for ``st.login()`` with a button. When ``provider`` is ``None``,
        Streamlit uses a default provider configured entirely under ``[auth]``.
        When ``provider`` is a name like ``"auth0"``, Streamlit uses
        ``redirect_uri`` and ``cookie_secret`` from ``[auth]``, and OIDC client
        settings from ``[auth.{provider}]``. The OAuth callback is always the
        ``redirect_uri`` in ``[auth]`` (Auth0 Allowed Callback URLs must match).

        Reference: https://docs.streamlit.io/develop/api-reference/user/st.login
    """
    if st.button(button_text, **kwargs):
        st.login(provider)
        return True
    return False


def logout_button(button_text: str = "Log out", **kwargs) -> bool:
    """
    Create a logout button that removes the authentication cookie.
    
    Parameters:
        button_text (str, optional): Text to display on the button. Defaults to "Log out".
        **kwargs: Additional keyword arguments passed to st.button().
                 Common options: use_container_width=True.
    
    Returns:
        bool: True if button was clicked, False otherwise.
    
    Example:
        >>> if is_user_logged_in():
        ...     if logout_button("Log out", use_container_width=True):
        ...         pass  # st.logout() handles the logout
    
    Note:
        Wrapper for st.logout() with customizable button. Calls st.logout() which
        removes the authentication cookie and starts a new session. The user will
        need to log in again to access protected content.
        
        Reference: https://docs.streamlit.io/develop/api-reference/user/st.logout
    """
    if st.button(button_text, **kwargs):
        st.logout()
        return True
    return False


def display_email_verification_ui() -> None:
    """
    Blocked UX for logged-in users whose Auth0 ``email_verified`` claim is not True.

    Renders account info from identity claims (avatar, name, email when present),
    explanatory copy for Hays Specifications Assistant, and a **Log out** control.
    Does **not** call the Auth0 Management API (no programmatic resend).
    """
    st.warning("Your account email has not been verified.")

    display_user_profile()

    st.markdown(
        """
Access to **Hays Specifications Assistant** continues only after Auth0 confirms your
account email as verified (`email_verified` must be True in your session).

**Next steps:**
1. Open the verification message Auth0 sent when you signed up (check spam folders).
2. Select **Verify Your Account** in that message.
3. **Log out** below and sign in again so your session picks up the updated claim.
        """
    )

    logout_button("Log out to refresh session", use_container_width=True, type="primary")

