import importlib
import re
import sys
import uuid
from pathlib import Path

import streamlit as st

import utils.app_state
import utils.chat_threads
import utils.pdf_citation_viewer
import utils.ui_theme
from utils.auth0_utils import (
    display_user_profile,
    get_user_attribute,
    get_user_email,
    is_user_logged_in,
    require_allowed_domain,
    require_login,
    require_verified_email,
)
from utils.config_vars import (
    CHAT_MODELS,
    DEFAULT_CHAT_MODEL,
    OPENAI_PRO_MODEL,
    about,
    system_prompt,
)
from utils.login_landing import (
    load_privacy_policy_text,
    render_guest_email_gate,
    render_hays_app_header,
)
from utils.docx_utils import convert_md_to_docx, get_docx_bytes
from utils.feedback_ui import feedback_buttons_fragment
from utils.llm_errors import format_llm_error
from utils.pdf_chat_ui import (
    append_user_message,
    generate_llm_response,
    get_attachment_names_for_log,
    init_pdf_session_state,
    pdf_checklist_nav_disabled,
    process_pending_pdf_uploads,
    render_pdf_uploader,
)
from utils.neon_utils import create_log_entry
from utils.pdf_citation_viewer import (
    build_pdf_filename_citation_suffix,
    render_citation_pdf_viewer,
    render_response_with_citation_actions,
)
from utils.questions import questions
from utils.text_cleanup import clean_llm_response, remove_context_tags, strip_html_tags


def _use_importlib_reloads() -> bool:
    """Return whether debug-time module reloading is enabled."""
    try:
        value = st.secrets.get("use_importlib", False)
    except Exception:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _reload_module_by_name(name: str):
    """Reload a local utils.* module; recover if Streamlit evicted sys.modules during reload (raises KeyError on Python 3.14+)."""
    mod = sys.modules.get(name)
    if mod is not None and sys.modules.get(name) is mod:
        try:
            fresh = importlib.reload(mod)
        except (ImportError, KeyError, ModuleNotFoundError):
            sys.modules.pop(name, None)
            fresh = importlib.import_module(name)
    else:
        sys.modules.pop(name, None)
        fresh = importlib.import_module(name)
    parent_name, _, attr = name.rpartition(".")
    if parent_name and attr:
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, attr, fresh)
    return fresh


def _reload_project_modules():
    """Reload local utils.* modules on Streamlit rerun; rebind names (star imports stay stale after reload)."""
    importlib.import_module("utils")
    _reload_module_by_name("utils.llm_prompts")
    cv = _reload_module_by_name("utils.config_vars")
    qu = _reload_module_by_name("utils.questions")
    dx = _reload_module_by_name("utils.docx_utils")
    tc = _reload_module_by_name("utils.text_cleanup")
    _reload_module_by_name("utils.allowed_users")
    _reload_module_by_name("utils.app_state")
    _reload_module_by_name("utils.chat_threads")
    pcv = _reload_module_by_name("utils.pdf_citation_viewer")
    _reload_module_by_name("utils.ui_theme")
    landing = _reload_module_by_name("utils.login_landing")
    a0 = _reload_module_by_name("utils.auth0_utils")
    nu = _reload_module_by_name("utils.neon_utils")
    fu = _reload_module_by_name("utils.feedback_ui")
    _reload_module_by_name("utils.openai_client")
    _reload_module_by_name("utils.spec_chunking")
    _reload_module_by_name("utils.vector_store")
    _reload_module_by_name("utils.retrieval")
    le = _reload_module_by_name("utils.llm_errors")
    gp = _reload_module_by_name("utils.pdf_chat_ui")

    g = globals()

    g["DEFAULT_CHAT_MODEL"] = cv.DEFAULT_CHAT_MODEL
    g["CHAT_MODELS"] = cv.CHAT_MODELS
    g["OPENAI_PRO_MODEL"] = cv.OPENAI_PRO_MODEL
    g["format_llm_error"] = le.format_llm_error
    g["system_prompt"] = cv.system_prompt
    g["about"] = cv.about
    g["questions"] = qu.questions

    g["convert_md_to_docx"] = dx.convert_md_to_docx
    g["get_docx_bytes"] = dx.get_docx_bytes
    g["clean_llm_response"] = tc.clean_llm_response
    g["remove_context_tags"] = tc.remove_context_tags
    g["strip_html_tags"] = tc.strip_html_tags

    g["append_user_message"] = gp.append_user_message
    g["generate_llm_response"] = gp.generate_llm_response
    g["get_attachment_names_for_log"] = gp.get_attachment_names_for_log
    g["pdf_checklist_nav_disabled"] = gp.pdf_checklist_nav_disabled
    g["process_pending_pdf_uploads"] = gp.process_pending_pdf_uploads
    g["render_pdf_uploader"] = gp.render_pdf_uploader
    g["init_pdf_session_state"] = gp.init_pdf_session_state
    g["render_citation_pdf_viewer"] = pcv.render_citation_pdf_viewer
    g["render_response_with_citation_actions"] = pcv.render_response_with_citation_actions

    g["require_login"] = a0.require_login
    g["require_verified_email"] = a0.require_verified_email
    g["require_allowed_domain"] = a0.require_allowed_domain
    g["display_user_profile"] = a0.display_user_profile
    g["get_user_email"] = a0.get_user_email
    g["get_user_attribute"] = a0.get_user_attribute
    g["is_user_logged_in"] = a0.is_user_logged_in
    g["render_guest_email_gate"] = landing.render_guest_email_gate
    g["render_hays_app_header"] = landing.render_hays_app_header

    g["create_log_entry"] = nu.create_log_entry

    g["feedback_buttons_fragment"] = fu.feedback_buttons_fragment


SAMPLE_PROMPT_GROUP_ORDER = [
    "Submittals",
    "Code/Standards",
    "Coordination",
    "RFI",
    "Closeout",
    "Other",
]

SAMPLE_PROMPT_GROUP_KEYWORDS = {
    "Submittals": (
        "submittal", "equipment", "switchgear", "transformer", "xfmr",
        "panel", "busway", "bus duct", "rack", "fitting", "box", "enclosure",
    ),
    "Code/Standards": (
        "code", "standard", "grade", "life safety", "critical",
        "emergency", "isolated power", "hazardous", "class i", "fire alarm",
    ),
    "Coordination": (
        "clearance", "spacing", "elevation", "sleeve", "penetrate",
        "exterior", "underground", "communication", "security", "audio visual",
        "av/it", "conduit", "raceway", "bend", "backfill", "stub",
    ),
    "RFI": ("rfi", "if so", "if not", "is there", "are there", "allowed"),
    "Closeout": ("closeout", "source pages", "urls", "summarize", "email", "table"),
}

FOLLOW_UP_ACTIONS = (    
    (
        "Draft email",
        "Write a brief, professional email to the project team about the topic of this conversation. Use a clear subject line, open with a salutation followed by a colon, then state the issue, the relevant specification reference, and the specific action or decision needed — no small talk. When you cite the specification, give the section, page, and source PDF filename, and sign as Hays Electrical Services (hayselectrical.com).",
        False,
    ),
    (
        "Draft RFI",
        "Draft a formal RFI about the topic of this conversation for the design team. List Hays Electrical Services as the requester and include: a brief RFI subject, the affected specification section(s), page(s), and source PDF filename(s), a clear description of the issue or conflict, the specific question, a proposed resolution where reasonable. Ground every reference in the uploaded PDF(s) and name the PDF for each citation.",
        False,
    ),
    (
        "Make checklist",
        "Create a field-ready electrical construction checklist for the topic of this conversation. List concise, verifiable items a foreman or engineer can check off, and pair each item with its specification reference — section, page, and source PDF filename — from the uploaded PDF(s) where applicable.",
        False,
    ),
    (
        "Show source excerpts",
        "For the topic of this conversation, quote the exact specification text that governs it, verbatim, with the section reference, page number, and source PDF filename from the uploaded PDF(s). Give one excerpt per requirement so it can be verified and cited directly. Keep it concise.",
        False,
    ),
    (
        "Find related sections",
        "Identify other specification sections in the uploaded PDF(s) related to the topic of this conversation, including cross-trade and coordination dependencies. For each, give the section reference, page, and source PDF filename, and briefly explain why it matters to electrical installation or coordination.",
        False,
    ),

)


def _sample_prompt_group(prompt: str) -> str:
    prompt_lower = prompt.lower()
    for group in SAMPLE_PROMPT_GROUP_ORDER:
        if group == "Other":
            continue
        if any(keyword in prompt_lower for keyword in SAMPLE_PROMPT_GROUP_KEYWORDS[group]):
            return group
    return "Other"


def _group_sample_prompts(prompt_list: list[str]) -> dict[str, list[tuple[int, str]]]:
    grouped = {group: [] for group in SAMPLE_PROMPT_GROUP_ORDER}
    for idx, prompt in enumerate(prompt_list):
        grouped[_sample_prompt_group(prompt)].append((idx, prompt))
    return {group: prompts for group, prompts in grouped.items() if prompts}


def _append_prompt_to_chat(user_prompt: str) -> bool:
    return append_user_message(user_prompt)


def _build_followup_prompt(base_prompt: str) -> str:
    names = st.session_state.get("loaded_pdf_names") or []
    suffix = build_pdf_filename_citation_suffix(names)
    if not suffix:
        return base_prompt
    return base_prompt + suffix


def _render_followup_buttons(message_index: int) -> None:
    st.caption("Quick follow-ups")
    with st.container(
        horizontal=True,
        gap="xxsmall",
        vertical_alignment="center",
        width="content",
    ):
        for label, followup_prompt, _use_kb in FOLLOW_UP_ACTIONS:
            if st.button(
                label,
                key=f"followup_{message_index}_{label.lower().replace(' ', '_')}",
                type="secondary",
                width="content",
            ):
                _append_prompt_to_chat(_build_followup_prompt(followup_prompt))


@st.fragment
def _render_collected_responses_fragment() -> None:
    st.markdown(
        """
        <style>
        div[class*="st-key-edit_"] {
            margin-bottom: -0.25rem;
        }

        div[class*="st-key-save_edit_"],
        div[class*="st-key-delete_"] {
            margin-top: 0;
            margin-bottom: -0.35rem;
        }

        div[class*="st-key-download_collected_"] {
            margin-top: -0.35rem;
            margin-bottom: -0.35rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Collected Responses"):
        _, clear_col = st.columns([3, 2], vertical_alignment="center")
        with clear_col:
            if st.button("Clear Responses", type="secondary", key="clear_answers_col", width="stretch"):
                st.session_state.collected_answers = []

        # Display collected answers with edit capability
        for i, entry in enumerate(st.session_state.collected_answers):
            question_title, prompt, answer, entry_id, _source_msg_idx = _unpack_collected_entry(entry)
            if question_title:
                st.markdown(f"### {question_title}")
            else:
                # Only show the prompt as a header for follow-up turns
                st.markdown(f"### {prompt}")

            # Calculate height based on content
            num_lines = len(answer.split('\n'))
            min_height = max(100, num_lines * 25)  # 25 pixels per line, minimum 100px

            # Make the answer editable with dynamic height
            edited_answer = st.text_area(
                "Edit Response",
                value=answer,
                height=min_height,
                key=f"edit_{entry_id}",
                label_visibility="collapsed"
            )

            # Add Save and Delete buttons for each answer with consistent spacing
            btn_col1, btn_col2 = st.columns([1, 1], gap="small")
            with btn_col1:
                if st.button("Save Edits",
                           key=f"save_edit_{entry_id}",
                           width="stretch"):
                    st.session_state.collected_answers[i] = (
                        question_title, prompt, edited_answer, entry_id, _source_msg_idx
                    )
                    st.success("Edit saved!")
            with btn_col2:
                if st.button("Delete",
                           key=f"delete_{entry_id}",
                           help="Delete this response",
                           width="stretch"):
                    st.session_state.collected_answers.pop(i)

        # Download buttons with consistent spacing
        if st.session_state.collected_answers:
            dl_col1, dl_col2 = st.columns([1, 1], gap="small")

            # Markdown download
            with dl_col1:
                collected_text = "\n".join([
                    f"### {qt if qt else prompt}\n\n{a}\n\n---"
                    for qt, prompt, a, _eid, _src in (
                        _unpack_collected_entry(item)
                        for item in st.session_state.collected_answers
                    )
                ])
                st.download_button(
                    label="Download as Markdown",
                    data=collected_text,
                    file_name="specification_responses.md",
                    mime="text/markdown",
                    key="download_collected_markdown",
                    width="stretch",
                )

            # Word document download
            with dl_col2:
                word_doc = create_word_document(st.session_state.collected_answers)
                if word_doc is not None:
                    st.download_button(
                        label="Download as Word",
                        data=word_doc,
                        file_name="specification_responses.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="download_collected_word",
                        width="stretch",
                    )
                else:
                    st.warning("Word export is unavailable for these responses. Markdown export is still available.")

def replace_dollar_with_fullwidth(text):
    return text.replace('$', '＄')


def _new_collected_entry_id() -> str:
    return uuid.uuid4().hex


def _unpack_collected_entry(entry: tuple) -> tuple:
    """Return (question_title, prompt, answer, entry_id, source_message_index)."""
    if len(entry) >= 5:
        return entry[0], entry[1], entry[2], entry[3], entry[4]
    if len(entry) == 4:
        return entry[0], entry[1], entry[2], entry[3], None
    if len(entry) == 3:
        return entry[0], entry[1], entry[2], _new_collected_entry_id(), None
    question, answer = entry[0], entry[1]
    prompt = question if question else "Follow-up"
    return question, prompt, answer, _new_collected_entry_id(), None


def _migrate_collected_answers(items: list) -> list:
    """Normalize collected answers to 5-tuple with stable entry_id."""
    migrated = []
    for item in items:
        if len(item) >= 5:
            migrated.append(item)
        elif len(item) == 4:
            migrated.append((item[0], item[1], item[2], item[3], None))
        elif len(item) == 3:
            migrated.append((item[0], item[1], item[2], _new_collected_entry_id(), None))
        elif len(item) == 2:
            question, answer = item
            migrated.append(
                (question, question if question else "Follow-up", answer, _new_collected_entry_id(), None)
            )
        else:
            migrated.append(item)
    return migrated


def _render_sidebar(
    *,
    history_actions_disabled: bool,
    checklist_nav_disabled: bool,
) -> None:
    """Sidebar: chat history, prompts, settings, checklist navigation, and help."""
    st.button(
        "＋ New chat",
        key="sidebar_new_chat",
        on_click=utils.chat_threads.queue_new_thread,
        disabled=history_actions_disabled,
        width="stretch",
        type="primary",
    )

    with st.expander("Recents", expanded=False):
        with st.container(
            key="hays_recents_scroll",
            height=288,
            border=False,
        ):
            utils.chat_threads.render_sidebar_history(
                disabled=history_actions_disabled
            )

    with st.expander("Sample Prompts", expanded=False):
        grouped_questions = _group_sample_prompts(questions)
        for group, group_questions in grouped_questions.items():
            st.markdown(f"#### {group}")
            for i, question in group_questions:
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"{i+1}. {question}")
                with col2:
                    if st.button("➜", key=f"q_{i}"):
                        st.session_state.process_question = question

    with st.expander("Settings", expanded=False):
        _render_chat_model_settings()

    with st.expander("Tools", expanded=False):
        st.caption(
            "Spec Checklist — auto-fill from the same uploaded PDF(s)."
        )
        if st.button(
            "Spec Checklist",
            key="nav_checklist",
            width="stretch",
            disabled=checklist_nav_disabled,
        ):
            st.switch_page("pages/spec_checklist.py")
        if checklist_nav_disabled:
            st.caption(
                "Wait for PDF upload to finish before opening Spec Checklist."
            )

    with st.expander("About", expanded=False):
        st.markdown(about)

    with st.expander("Account", expanded=False):
        if is_user_logged_in():
            display_user_profile(sidebar_account=True)
        else:
            st.markdown(f"**Email:** {get_user_email() or ''}")
            if st.button("Change email", key="change_guest_email"):
                st.session_state.pop("guest_email", None)
                st.rerun()

    with st.expander("Privacy Policy", expanded=False):
        st.markdown(load_privacy_policy_text())


def _render_chat_model_settings() -> None:
    """Sidebar model picker for OpenAI chat answers."""
    st.session_state.selected_model = st.selectbox(
        "Model",
        CHAT_MODELS,
        index=CHAT_MODELS.index(st.session_state.selected_model),
        help=(
            f"Default: **{DEFAULT_CHAT_MODEL}**. "
            f"**{OPENAI_PRO_MODEL}** is optional for deeper reasoning (slower, higher cost)."
        ),
    )


def _neon_log(user_prompt, llm_response_snippet):
    """Persist a prompt/response row to Neon and return the created dict (or None)."""
    try:
        return create_log_entry(
            user_email=get_user_email() or "unknown@unknown",
            user_name=get_user_attribute("name"),
            user_nickname=get_user_attribute("nickname"),
            user_prompt=user_prompt,
            llm_response_snippet=llm_response_snippet,
            model=st.session_state.get("selected_model", DEFAULT_CHAT_MODEL),
            attachment_names=get_attachment_names_for_log(),
        )
    except Exception:
        return None


def _user_query_text(prompt_text: str) -> str:
    m = re.search(r"<query>(.*?)</query>", prompt_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return (prompt_text or "").strip()


def generate_response(prompt_text, include_history=True):
    """Stream the OpenAI chat response for the current user message."""
    original_query = _user_query_text(prompt_text)
    full_response = ""
    with st.spinner("Searching specifications and generating response…"):
        for chunk in generate_llm_response(prompt_text, include_history=include_history):
            full_response = chunk
            yield chunk
    if full_response:
        log_entry = _neon_log(
            user_prompt=original_query,
            llm_response_snippet=clean_llm_response(full_response),
        )
        if log_entry:
            st.session_state["last_log_id"] = log_entry["id"]


# Word document creation using docx_utils
def create_word_document(collected_answers):
    """Create Word document using docx_utils for better formatting"""
    try:
        # Create markdown content from collected answers
        markdown_content = ""
        for entry in collected_answers:
            question_title, prompt, answer, _entry_id, _source_idx = _unpack_collected_entry(entry)
            title = question_title if question_title else prompt
            markdown_content += f"# {title}\n\n{answer}\n\n---\n\n"
        
        # Convert to Word document using docx_utils
        doc = convert_md_to_docx(markdown_content)
        
        # Convert to bytes
        return get_docx_bytes(doc)
        
    except Exception as e:
        st.error(f"Error creating Word document: {str(e)}")
        return None



def main():
    if _use_importlib_reloads():
        _reload_project_modules()

    st.set_page_config(
        page_title="Hays Specifications Assistant",
        page_icon="img/favicon.ico",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if not render_guest_email_gate():
        st.stop()

    utils.app_state.bootstrap()
    utils.ui_theme.inject_gpt_inspired_layout_css()

    if st.session_state.get("selected_model") not in CHAT_MODELS:
        st.session_state.selected_model = DEFAULT_CHAT_MODEL

    # Initialize session state variables
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'collected_answers' not in st.session_state:
        st.session_state.collected_answers = []
    st.session_state.pop("chat_history", None)
    # Initialize system prompt if not already present
    if 'system_prompt' not in st.session_state:
        st.session_state.system_prompt = system_prompt

    if "collected_answers" in st.session_state and not st.session_state.get(
        "_migrated_collected_answers"
    ):
        st.session_state.collected_answers = _migrate_collected_answers(
            st.session_state.collected_answers
        )
        st.session_state._migrated_collected_answers = True

    current_user_email = get_user_email() or ""
    current_model = st.session_state.get("selected_model")

    utils.chat_threads.apply_initial_blank_state()
    utils.chat_threads.consume_pending_actions(
        current_user_email,
        model=current_model,
    )
    utils.chat_threads.ensure_active_thread(
        current_user_email,
        model=current_model,
    )
    pending_response = bool(
        st.session_state.get("messages")
        and st.session_state.messages[-1][0] == "user"
    )
    if not pending_response:
        utils.chat_threads.persist_active_thread(current_user_email)
    utils.chat_threads.refresh_thread_list(current_user_email)

    init_pdf_session_state()
    process_pending_pdf_uploads()

    with st.sidebar:
        history_actions_disabled = bool(
            st.session_state.get("messages")
            and st.session_state.messages[-1][0] == "user"
        )
        _render_sidebar(
            history_actions_disabled=history_actions_disabled,
            checklist_nav_disabled=pdf_checklist_nav_disabled(),
        )

    # Main chat area. The right column stays wide enough for the PDF page.
    with st.container(key="hays_workspace"):
        col1, col2 = st.columns([1.15, 1], gap="medium")

        with col1:
            render_hays_app_header()

            render_pdf_uploader()

            if hasattr(st.session_state, "process_question"):
                sample_question = (st.session_state.get("process_question") or "").strip()
                if sample_question:
                    append_user_message(sample_question)
                del st.session_state.process_question

            # Display chat messages
            # Counter that tracks the zero-based index of each assistant message
            # among assistant messages only. It is both the qa_index (join key
            # into log_entry_ids / feedback_states) and the assistant_message_index
            # used to build unique Streamlit widget keys.
            assistant_counter = -1
            for i, message_data in enumerate(st.session_state.messages):
                role = message_data[0]
                message = message_data[1]
                search_terms = message_data[2] if len(message_data) > 2 else None
                explanation = message_data[3] if len(message_data) > 3 else None

                if role == "assistant" and not (message or "").strip():
                    continue

                if role == "assistant":
                    assistant_counter += 1

                message_container = st.chat_message(role, avatar="img/user.png" if role == "user" else "img/ai.png")
                with message_container:
                    message_clean0 = remove_context_tags(message)
                    message_clean1 = strip_html_tags(message_clean0)
                    if role == "assistant":
                        message_clean1 = clean_llm_response(message_clean1)
                    message_clean2 = replace_dollar_with_fullwidth(message_clean1)
                    if role == "assistant":
                        render_response_with_citation_actions(message_clean2, f"message_{i}")
                    else:
                        st.markdown(message_clean2)

                    if role == "assistant":
                        qa_index = assistant_counter
                        has_log_entry = qa_index in st.session_state.log_entry_ids
                        feedback_buttons_fragment(
                            qa_index, qa_index, has_log_entry,
                            message_index=i, questions=questions,
                        )
                        _render_followup_buttons(i)

            # Generate new response if there's a new message
            if st.session_state.messages and st.session_state.messages[-1][0] == "user":
                with st.chat_message("assistant", avatar="img/ai.png"):
                    response_placeholder = st.empty()
                    final_response = ""
                    try:
                        for response_chunk in generate_response(st.session_state.messages[-1][1]):
                            final_response = response_chunk
                            response_placeholder.markdown(
                                replace_dollar_with_fullwidth(response_chunk)
                            )

                        final_response_clean = clean_llm_response(final_response)
                        if final_response_clean != final_response:
                            response_placeholder.markdown(
                                replace_dollar_with_fullwidth(final_response_clean)
                            )

                        if not (final_response_clean or "").strip():
                            response_placeholder.empty()
                            # Placeholder assistant turn prevents re-generation on rerun.
                            st.session_state.messages.append(("assistant", "", None))
                            utils.chat_threads.persist_active_thread(
                                current_user_email,
                                force=True,
                            )
                            utils.chat_threads.refresh_thread_list(current_user_email)
                        else:
                            st.session_state.messages.append(("assistant", final_response_clean, None))
                            # Bind the log row id to the newly-appended assistant
                            # message's qa_index so the feedback fragment can update it.
                            # qa_index = count of assistant messages - 1.
                            new_qa_index = sum(1 for m in st.session_state.messages if m[0] == "assistant") - 1
                            new_log_id = st.session_state.pop("last_log_id", None)
                            if new_log_id:
                                st.session_state.log_entry_ids[new_qa_index] = new_log_id
                            utils.chat_threads.persist_active_thread(
                                current_user_email,
                                force=True,
                            )
                            utils.chat_threads.refresh_thread_list(current_user_email)
                            # Render feedback buttons for the just-streamed response so
                            # the user sees them without needing another interaction.
                            has_log_entry = new_qa_index in st.session_state.log_entry_ids
                            new_msg_index = len(st.session_state.messages) - 1
                            response_placeholder.empty()
                            render_response_with_citation_actions(
                                replace_dollar_with_fullwidth(final_response_clean),
                                f"message_{new_msg_index}",
                            )
                            feedback_buttons_fragment(
                                new_qa_index, new_qa_index, has_log_entry,
                                message_index=new_msg_index, questions=questions,
                            )
                            _render_followup_buttons(new_msg_index)
                    except Exception as e:
                        error_text = format_llm_error(e)
                        st.error(error_text)
                        # Append the error as an assistant message so the user can
                        # still flag bad failures with a thumbs-down. Log it too
                        # and wire the new row id into log_entry_ids.
                        error_log = _neon_log(
                            user_prompt="show_response_error",
                            llm_response_snippet=str(e)[:200],
                        )
                        st.session_state.messages.append(("assistant", error_text, None))
                        err_qa_index = sum(1 for m in st.session_state.messages if m[0] == "assistant") - 1
                        if error_log and error_log.get("id"):
                            st.session_state.log_entry_ids[err_qa_index] = error_log["id"]
                        utils.chat_threads.persist_active_thread(
                            current_user_email,
                            force=True,
                        )
                        utils.chat_threads.refresh_thread_list(current_user_email)
                        has_log_entry = err_qa_index in st.session_state.log_entry_ids
                        err_msg_index = len(st.session_state.messages) - 1
                        feedback_buttons_fragment(
                            err_qa_index, err_qa_index, has_log_entry,
                            message_index=err_msg_index, questions=questions,
                        )

            # Bottom controls in compact columns: chat input, clear chat.
            # Per-response "Save" lives in the feedback row for each assistant
            # message (see utils/feedback_ui.feedback_buttons_fragment).
            has_assistant_response = any(
                m[0] == "assistant" for m in st.session_state.messages
            )

            chat_input_col, chat_clear_col = st.columns(
                [9, 1.2], gap="small", vertical_alignment="bottom"
            )
            with chat_input_col:
                user_prompt = st.chat_input("Ask about the uploaded specification PDF(s)…")
            with chat_clear_col:
                st.button(
                    "Clear Chat",
                    key="clear_chat_inline",
                    type="secondary",
                    width="stretch",
                    on_click=utils.chat_threads.queue_new_thread,
                    disabled=not has_assistant_response,
                )

            if user_prompt:
                _append_prompt_to_chat(user_prompt)
                st.rerun()

        # Right column for collected answers
        with col2:
            _render_collected_responses_fragment()
            render_citation_pdf_viewer()

if __name__ == "__main__":
    main()
