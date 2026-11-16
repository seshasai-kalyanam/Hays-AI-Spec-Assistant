# Suggestions for `app.py` and Gemini Cloud API Usage

After reviewing `app.py`, `utils/gemini_pdf_ui.py`, and `utils/gemini_client.py`, here are some suggestions for improving the architecture, robustness, and usage of the Gemini Cloud APIs.

## 1. Gemini Cloud API Usage

### File Lifecycle Management (Missing Cleanup)

Currently, when a user uploads a PDF, it is sent to the Gemini File API via `upload_streamlit_pdf`. If the user removes the PDF from the Streamlit uploader, `_sync_removed_uploaded_pdfs()` updates the `st.session_state` to remove the references but **does not explicitly delete the file from Gemini servers**. 

- **Suggestion**: Call `client.files.delete(name=gemini_file.name)` when a file is removed from the UI. This prevents leaking files that count against the project's storage quota, even though they will eventually expire.





### Thinking Config Nuances

The methods `_get_chat_thinking_level` and `_get_checklist_thinking_level` make assumptions about `thinking_level` support based on string matching (e.g., `"3.5"`, `"3.1"`).

- **Suggestion**: Ensure that only models explicitly verified to support `ThinkingConfig` receive this parameter. Passing it to an unsupported model will throw an API error, triggering the fallback or failing the chat response entirely.

## 2. General `app.py` & UI Architecture





### Redundant State Migration

The function `_migrate_collected_answers()` runs on every single Streamlit rerun if `collected_answers` is in the session state.

- **Suggestion**: Set a flag like `st.session_state._migrated_answers = True` after the first migration to prevent looping over the list on every rerun.

### Document/State Initialization

Using `setdefault` repeatedly in `init_pdf_session_state` is fine, but since Streamlit handles reruns often, keeping state initialization centralized at the very top of `app.py` (which you do partially) avoids bugs where states reset unpredictably during deep nested function calls.

## 3. Application & UI Caching (`pages/spec_checklist.py` & Schema)

### Cache Heavy File/Schema Reads (`@st.cache_data`)

Currently, `get_active_checklist_fields()` (in `utils/results_form.py`) which calls `load_prompts_csv()` and `attach_docx_refs()` (in `utils/checklist_schema.py`) is executed on Streamlit reruns. 
These methods perform synchronous, blocking I/O: parsing the `db/prompts.csv` file and reading/parsing the `Hays Spec. Review Checklist.docx` Word document via `python-docx` on almost every interaction.

- **Suggestion**: Decorate `get_active_checklist_fields()` with `@st.cache_data`. This ensures Streamlit only parses the CSV and Word Document once per server lifecycle, rather than forcing every single user interaction (like checking a box or opening an expander) to re-read files from disk.

```python
import streamlit as st

@st.cache_data
def get_active_checklist_fields() -> list[ChecklistField]:
    fields = load_prompts_csv(CHECKLIST_PROMPTS_CSV)
    return attach_docx_refs(fields, CHECKLIST_TEMPLATE_DOCX)
```

*(Note: Be sure your dataclasses like `ChecklistField` are compatible with Streamlit's hashing/pickling, which they naturally should be).*

### Cache Checklist Statistics

In `_render_about_this_tool()` in `pages/spec_checklist.py`, `count_prompts_csv()` is executed to render the help text:

```python
f"Home page (~{count_prompts_csv()} structured prompts from `db/prompts.csv`)."
```

- **Suggestion**: This function currently opens the CSV on every render of the "About this tool" expander. This should be cached via `@st.cache_data` or, better yet, it should just return the `len(get_active_checklist_fields())` to avoid opening the CSV multiple times.

## 4. Gemini Cloud API Context Caching

### Context Cache Resource Leaking

In `utils/gemini_client.py`, `get_or_create_context_cache` stores the active context caches in `st.session_state.gemini_context_caches`. 
When a user switches the model or uploads a new set of PDFs, the cache key changes, and a **new** Context Cache is created via `client.caches.create`. 
The problem is that the **old cache is never explicitly deleted** from the Gemini servers via `client.caches.delete(name=...)`. Furthermore, if the user closes their browser tab, the Streamlit session dies, but the Gemini API is completely unaware, leaving the context cache active on Google's servers.

- **Suggestion**: Context Caching incurs hourly costs as long as the cache is active. You should implement logic to explicitly delete old caches when the user switches models or files. For session endings, you might need to lower the default TTL to something shorter (e.g. `15m`) and proactively refresh the TTL (`client.caches.update`) on active interactions to minimize orphan cache billing.

### System Prompt Granularity and Cache Busting

The cache key is `(model, sorted_file_names, system_instruction)`. 
If the application dynamically alters the `system_instruction` based on user state or small variables, it will constantly bust the cache and create entirely new cached resources. 

- **Suggestion**: Ensure that `build_pdf_checklist_batch_system_prompt()` and similar functions return completely static strings. Any dynamic context (like user names, dates, or specific row data) should be passed in the `user` message part of the request, NOT the system prompt, so you can maximize cache hit rates across multiple requests.

