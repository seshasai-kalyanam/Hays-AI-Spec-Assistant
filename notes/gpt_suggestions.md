# app.py Review Suggestions

## Findings

1. **P1 - Streaming assistant output bypasses the display-only dollar sanitization.**  
   `app.py:631` and `app.py:635` render `response_chunk` / `final_response_clean` directly with `response_placeholder.markdown(...)`. The normal chat-history render path uses `replace_dollar_with_fullwidth(...)` before `st.markdown(...)` at `app.py:605-606`, so a newly generated answer can still render `$...$` as LaTeX until a later full rerun.  
   **Suggestion:** apply the same display-only sanitizer before both placeholder renders, while keeping the stored `final_response_clean` unchanged for exports/logging.

2. **P2 - Collected-response widgets use list indexes as Streamlit keys.**  
   `_render_collected_responses_fragment()` iterates with `enumerate(...)` at `app.py:271`, then keys widgets with `edit_{i}`, `save_edit_{i}`, and `delete_{i}` around `app.py:283-305`. After deleting an item, later responses shift indexes and can inherit stale widget state, which can show, save, or delete the wrong response.  
   **Suggestion:** store a stable id with each collected response and use that id in widget keys. If changing the data shape is too large, clear the affected widget keys immediately after a delete.

3. **P2 - Editing a collected response breaks the saved-state identity.**  
   `app.py:297` replaces the collected answer text with the edited text. `utils/feedback_ui.py:34-38` detects whether a response is saved by comparing the saved answer text to the original assistant message. Once edited, the original assistant message no longer matches and the same response can appear unsaved and be saved again as a duplicate.  
   **Suggestion:** keep editable answer text separate from the source assistant identity, such as a source message index or generated response id, and make `_find_saved_index(...)` use that identity instead of answer text.

4. **P2 - Collected-response changes are marked dirty but are not persisted with chat threads.**  
   Clear/save/delete paths set `thread_dirty` in `app.py:267-306`, but `utils/chat_threads.py:158-164` serializes only `recents` and `log_entry_ids`, and `utils/chat_threads.py:192` resets `collected_answers` on thread hydration. If the panel is meant to survive thread switches or reloads, these edits are lost despite being marked dirty.  
   **Suggestion:** either serialize/hydrate `collected_answers` with the thread state or treat them as session-only and remove the `thread_dirty` updates from collected-response operations.

5. **P3 - Debug import reloading omits some imported helpers.**  
   `_reload_project_modules()` starts at `app.py:77` and rebinds several imported names, but it does not reload/rebind `utils.gemini_pdf_ui` functions imported at `app.py:33-37`, nor `remove_context_tags` / `strip_html_tags` imported at `app.py:41`. With `use_importlib` enabled, edits to those helpers can remain stale until the app process restarts.  
   **Suggestion:** reload `utils.gemini_pdf_ui` and rebind `append_user_message`, `generate_gemini_response`, `get_attachment_names_for_log`, and `render_pdf_uploader`; also rebind `remove_context_tags` and `strip_html_tags` from `utils.text_cleanup`.

# pages/spec_checklist.py Review Suggestions

## Findings

1. **P1 - Clear does not actually reset not-in-spec or error rows.**  
   `_clear_field_value(...)` clears `checklist_values`, `checklist_field_status`, footnote metadata, and parse warnings at `pages/spec_checklist.py:412-417`, but it leaves `checklist_autofill_log` intact. `_field_status(...)` then falls back to `_latest_autofill_entry_for_row(...)` at `pages/spec_checklist.py:278-285`, and `resolve_field_status(...)` preserves `cannot_answer_from_kb` / `error` from that latest log entry. The result is that a user can click **Clear** on a not-in-spec or error row and still see the row remain not-in-spec/error, contrary to the UI text that says Clear resets status and footnotes.  
   **Suggestion:** when clearing a row, either remove/invalidate that row's latest auto-fill log entry for status resolution, or track an explicit "cleared after autofill" row set and make `_field_status(...)` ignore `latest_log_state` for those rows.

2. **P1 - Manual edits can export stale auto-fill footnotes.**  
   Manual choice/text edits call `_clear_footnote_meta_for_row(...)` at `pages/spec_checklist.py:1146`, `pages/spec_checklist.py:1153`, `pages/spec_checklist.py:1183`, and `pages/spec_checklist.py:1196`. However, `_footnote_meta_for_export()` starts from `checklist_footnote_meta` and then rebuilds metadata from every `checklist_autofill_log` entry at `pages/spec_checklist.py:92-116`. That reintroduces the prior auto-fill explanation/citations after the user has manually changed the answer, so preview/Markdown/Word exports can attach old support to a new manual value.  
   **Suggestion:** make manual edits invalidate the row in both the metadata store and the export merge source. A small `checklist_footnote_cleared_rows` set, checked inside `_footnote_meta_for_export()`, would keep the last-run log visible without using it for current exports.

3. **P2 - DOCX export cache ignores the fields signature and template mtime.**  
   `_cached_export_docx(...)` is decorated with `@st.cache_data` at `pages/spec_checklist.py:1434-1440`, but the cache-busting parameters are named `_fields_sig` and `_template_mtime`. Streamlit treats underscore-prefixed cache parameters as excluded from hashing, so changes to prompt/template shape or template mtime may not invalidate cached Word bytes. The function also reloads fields from disk at `pages/spec_checklist.py:1442` instead of using the current `fields` passed by the caller at `pages/spec_checklist.py:1568-1573`.  
   **Suggestion:** rename those parameters to `fields_sig` and `template_mtime` so they participate in the cache key, and avoid reloading fields inside the cached function unless that reload is itself part of the cache key.

4. **P3 - The help text promises retrieval details that this page no longer captures or renders.**  
   The sidebar says the last auto-fill details include "search keywords" and "retrieved context" at `pages/spec_checklist.py:164-166`, but current batch results come from `run_checklist_batch_autofill(...)`, which returns parsed answer/explanation/citations without `retrieved_context` or `search_terms` in `utils/results_form.py:190-207`. `_apply_autofill_result(...)` then composes footnotes with empty context/search strings at `pages/spec_checklist.py:1004-1012`. Users looking for source excerpts or search details will not find them.  
   **Suggestion:** either update the help/log wording to match direct-PDF batch extraction, or add real retrieval/search fields to the result payload and render them in the last-run details.

## Verification

Review only. No app code was changed and no tests were run.

