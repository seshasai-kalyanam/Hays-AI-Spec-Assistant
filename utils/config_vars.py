# config_vars.py

import warnings

import streamlit as st

from utils.llm_prompts import (
    CHECKLIST_BATCH_SIZE,
    OPENAI_DEFAULT_MODEL,
    OPENAI_EMBEDDING_MODEL,
    OPENAI_MODELS,
    OPENAI_PRO_MODEL,
    build_pdf_chat_system_prompt,
)

warnings.filterwarnings("ignore", message=".*ScriptRunContext.*")


LOGIN_PAGE_INTRO = """Query and review construction specification documents for electrical and related building projects.

Upload specification PDFs on the home page. Chat answers and checklist auto-fill stay grounded in those documents—with section and page references where applicable."""

LOGIN_PAGE_FEATURES_CHAT = """
- **PDF upload** — one or more specification PDFs on Home; the same files are used on the checklist page
- **Interactive chat** — grounded answers over uploaded PDFs; optional higher-reasoning model in **Settings**
- **Quick follow-ups** — draft email, draft RFI, make checklist, show source excerpts, find related sections
- **Response feedback** — helpful / not helpful per answer
- **Collected Responses** — save, edit, and export replies as Markdown or Word
- **Recents** — reopen prior threads, start a new chat, rename inline, soft-delete from visible history
"""

LOGIN_PAGE_FEATURES_CHECKLIST = f"""
- Structured checklist (~384 prompts) aligned to the official Hays Word template
- **Auto-fill** selected fields from uploaded PDF(s) in batches of {CHECKLIST_BATCH_SIZE}
- Only **new** checked fields run each batch; **Clear** a field to re-run
- Review **Needs review** and **Not found in specification** rows with citation footnotes
- **Export** — Word (.docx) or Markdown: full checklist, answered only, or not-in-specification only
"""

LOGIN_PAGE_SIGN_IN_NOTE = """
Sign in with your authorized Hays account (Auth0). Prompt and response activity may be logged when logging is enabled.
"""

CHAT_MODELS = list(OPENAI_MODELS)
DEFAULT_CHAT_MODEL = OPENAI_DEFAULT_MODEL
CHECKLIST_MODELS = list(OPENAI_MODELS)
CHECKLIST_DEFAULT_MODEL = OPENAI_DEFAULT_MODEL

system_prompt = build_pdf_chat_system_prompt()

about = f"""
**What this is**

Query and review construction document specifications for electrical and related building projects. Uploaded **specification PDFs** are indexed page by page into a vector store (Neon pgvector, **{OPENAI_EMBEDDING_MODEL}**); each question retrieves the most relevant sections by meaning and exact keywords. Section and page references are included where applicable.

**Specifications chat**

- **PDF upload** — one or more specification PDFs on Home (thousands of pages supported); the same files are used on the checklist page
- **Interactive chat** — grounded answers from **{OPENAI_DEFAULT_MODEL}** over retrieved excerpts; optional **{OPENAI_PRO_MODEL}** in **Settings**
- **Quick follow-ups** — draft email, draft RFI, make checklist, show source excerpts, find related sections
- **Response feedback** — helpful / not helpful per answer (stored in Neon when logging is enabled)
- **Collected Responses** — save, edit, and export individual replies as Markdown or Word (right column)
- **Recents** (sidebar) — reopen prior threads, **＋ New chat**, rename inline, soft-delete from visible history
- **Sample Prompts** (sidebar) — send predefined questions to the chat

**Spec. Compliance Review Checklist**

- Structured checklist (~384 prompts from `db/prompts.csv`) aligned to the official Hays Word template
- **Auto-fill** selected fields from the same uploaded PDF(s) in **batches of {CHECKLIST_BATCH_SIZE}** via OpenAI, each field using its own retrieved excerpts
- Only **new** checked fields run on each auto-fill batch; **Clear** a field and re-run to refresh a row
- Review **Needs review** and **Not found in specification** rows; footnotes with citations
- **Export** — Word (.docx) or Markdown: full checklist, answered only, or not-in-specification only
- Open from **Tools** in the sidebar

**Authentication and logging**

- **Authentication** — Auth0 login with domain and allowlisted-email checks (**Account** in the sidebar)
- **Prompt logging** — optional chat logging in Neon PostgreSQL when configured

Hays Specifications Assistant · © 2026
"""

CHECKLIST_PROMPTS_CSV = "db/prompts.csv"
CHECKLIST_TEMPLATE_DOCX = "notes/Hays Spec. Review Checklist.docx"
