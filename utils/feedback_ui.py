"""Per-response thumbs up / thumbs down feedback UI.

Renders a Streamlit fragment next to each assistant message that lets the user
flag the response as helpful (value ``2``) or not helpful (value ``1``). The
chosen state is mirrored into ``st.session_state.feedback_states[qa_index]``
and persisted to the existing Neon ``chat_logs`` row via
:func:`utils.neon_utils.update_log`.

All database calls are best-effort: failures are logged via a named logger
(``hays.ui.feedback``) and never raise back into the chat render path.
"""

from __future__ import annotations

import logging
import uuid
from typing import Iterable

import streamlit as st

from utils.text_cleanup import remove_context_tags, strip_html_tags

logger = logging.getLogger("hays.ui.feedback")


def _find_preceding_user_prompt_clean(message_index: int) -> str | None:
    """Walk backwards from ``message_index`` to find the user prompt and clean it."""
    messages = st.session_state.get("messages", [])
    for j in range(message_index - 1, -1, -1):
        if messages[j][0] == "user":
            return strip_html_tags(remove_context_tags(messages[j][1]))
    return None


def _find_saved_index(source_message_index: int | None) -> int | None:
    """Return the index in ``collected_answers`` for this assistant message, or None."""
    if source_message_index is None:
        return None
    for i, ans in enumerate(st.session_state.get("collected_answers", [])):
        if len(ans) >= 5 and ans[4] == source_message_index:
            return i
    return None


def _hydrate_feedback_state(qa_index: int, has_log_entry: bool) -> None:
    """Populate ``feedback_states[qa_index]`` from Neon on first view.

    Runs only when the key is missing from session state AND a log id exists
    for this qa_index; guarantees that the correct caption appears after a
    page reload even though nothing else in session state survived.
    """
    if qa_index in st.session_state.feedback_states:
        return
    if not has_log_entry:
        return
    log_id = st.session_state.log_entry_ids.get(qa_index)
    if not log_id:
        return
    try:
        from utils.neon_utils import read_log_by_id

        log_entry = read_log_by_id(log_id)
        if log_entry:
            value = log_entry.get("feedback")
            st.session_state.feedback_states[qa_index] = value if value is not None else 0
    except Exception:
        logger.exception("read_log_by_id failed for qa_index=%s", qa_index)


def _record_feedback(qa_index: int, has_log_entry: bool, value: int) -> None:
    """Persist feedback to Neon (best-effort) and mirror it into session state.

    Session state is updated even when the DB write fails so the UI remains
    responsive; the Neon row will simply lag behind until the next successful
    click.
    """
    try:
        if has_log_entry and qa_index in st.session_state.log_entry_ids:
            log_id = st.session_state.log_entry_ids[qa_index]
            from utils.neon_utils import update_log

            update_log(log_id=log_id, feedback=value)
    except Exception:
        logger.exception("update_log failed for qa_index=%s value=%s", qa_index, value)
    st.session_state.feedback_states[qa_index] = value


@st.fragment
def feedback_buttons_fragment(
    qa_index: int,
    assistant_message_index: int,
    has_log_entry: bool,
    message_index: int,
    questions: Iterable[str] = (),
) -> None:
    """Render thumbs up / thumbs down and a save-toggle for one assistant message.

    The fragment boundary means each click re-renders only these buttons and
    their caption, not the full chat loop (which would otherwise re-stream
    LLM output on Streamlit >= 1.32).
    """
    try:
        _hydrate_feedback_state(qa_index, has_log_entry)

        current = st.session_state.feedback_states.get(qa_index, 0)

        messages = st.session_state.get("messages", [])
        assistant_msg = (
            messages[message_index][1]
            if 0 <= message_index < len(messages)
            and messages[message_index][0] == "assistant"
            else None
        )
        saved_idx = _find_saved_index(message_index) if assistant_msg else None

        # Horizontal row: [up][down][caption][spacer][save]. Give save a wider
        # final column so its label stays on one line.
        up_col, down_col, caption_col, spacer_col, save_col = st.columns(
            [1, 1, 3, 1, 6], vertical_alignment="center", gap="small"
        )
        with up_col:
            up_active = current == 2
            if st.button(
                "\U0001F44D",
                key=f"thumbs_up_{assistant_message_index}",
                type="primary" if up_active else "tertiary",
                help="Mark this response as helpful",
                use_container_width=True,
            ):
                _record_feedback(qa_index, has_log_entry, value=2)
                st.rerun(scope="fragment")

        with down_col:
            down_active = current == 1
            if st.button(
                "\U0001F44E",
                key=f"thumbs_down_{assistant_message_index}",
                type="primary" if down_active else "tertiary",
                help="Mark this response as not helpful",
                use_container_width=True,
            ):
                _record_feedback(qa_index, has_log_entry, value=1)
                st.rerun(scope="fragment")

        with caption_col:
            if current == 2:
                st.caption("You logged this response helpful")
            elif current == 1:
                st.caption("You logged this response not helpful")
            else:
                st.caption("Was this response helpful?")

        with spacer_col:
            st.empty()

        with save_col:
            if assistant_msg is None:
                st.empty()
            elif saved_idx is None:
                if st.button(
                    "\U0001F4BE Save chat response to Collected Responses",
                    key=f"save_response_{assistant_message_index}",
                    type="tertiary", width="stretch",
                    help="Save this response to Collected Responses",
                    use_container_width=True,
                ):
                    prev_clean = _find_preceding_user_prompt_clean(message_index)
                    question_set = set(questions) if questions else set()
                    entry_id = uuid.uuid4().hex
                    if prev_clean and prev_clean in question_set:
                        st.session_state.collected_answers.append(
                            (prev_clean, prev_clean, assistant_msg, entry_id, message_index)
                        )
                    else:
                        st.session_state.collected_answers.append(
                            (None, prev_clean, assistant_msg, entry_id, message_index)
                        )
                    st.rerun()
            else:
                if st.button(
                    "\U0001F4BE Saved",
                    key=f"save_response_{assistant_message_index}",
                    type="tertiary",
                    help="Click to remove from Collected Responses",
                    use_container_width=True,
                ):
                    try:
                        st.session_state.collected_answers.pop(saved_idx)
                    except IndexError:
                        logger.exception(
                            "collected_answers.pop failed for saved_idx=%s", saved_idx
                        )
                    st.rerun()

    except Exception:
        logger.exception("feedback_buttons_fragment failed for qa_index=%s", qa_index)
