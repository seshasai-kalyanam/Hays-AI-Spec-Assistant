# Hays Specifications Assistant

Streamlit app for querying and reviewing construction specification PDFs for electrical and related building projects. Uploaded PDFs are indexed in Neon Postgres (pgvector). Questions search that index and are answered by OpenAI from the retrieved excerpts.

Maintained by Hays Electrical Services. Contact: [sesha.kalyanam@hayselectrical.com](mailto:sesha.kalyanam@hayselectrical.com).

Live app: [hays-ai-spec-assistant-feqtolhixqjrsbc47rppmm.streamlit.app](https://hays-ai-spec-assistant-feqtolhixqjrsbc47rppmm.streamlit.app)

## How documents are used

- Each signed-in user uploads PDFs in their own browser session. The uploader starts empty for every visitor.
- A new PDF is read page by page, split on specification sections, and embedded with `text-embedding-3-large`. Vectors are stored in Neon.
- Uploading the same file again reuses the stored index. A question searches only the documents attached in that session, so two users with different PDFs do not see each other's text.
- Scanned PDFs with no text layer are rejected. Image-only pages need OCR before upload.
- An upload that stops partway through can be removed with **Delete unfinished indexes** under the uploader. Ready indexes are kept.

## Specifications chat

The Home page, `app.py`, is the chat interface.

- Upload one or more specification PDFs. The same session files are used on the checklist page. Large specifications (thousands of pages) are supported.
- Ask questions against the attached PDFs. The default model is `gpt-6.1-sol`. Settings also offers `gpt-6.1-terra` and `gpt-6-astra`.
- Follow-ups: draft email, draft RFI, make checklist, show source excerpts, and find related sections.
- Open cited pages in the right-column PDF viewer. A **View page** action jumps to that page. The viewer is `utils/pdf_citation_viewer.py` and uses [`streamlit-pdf-viewer`](https://pypi.org/project/streamlit-pdf-viewer/).
- Save replies into Collected Responses, edit them, and export Markdown or Word.
- Reopen recent chat threads, start a new chat, rename a thread, or remove it from visible history.
- Helpful / not helpful feedback is stored in Neon with the prompt log.

## Spec. Compliance Review Checklist

`pages/spec_checklist.py` fills the Hays checklist from the PDFs attached on Home.

- Loads the checklist prompts from `db/prompts.csv`, aligned to the Hays Word template.
- Search, filter, and select fields for auto-fill.
- Each field is searched in the index. OpenAI answers up to 10 fields per call from the retrieved excerpts.
- Later runs skip rows that already have answers. Clear a row to reset it.
- Statuses include answered, Needs review, Not found in specification, and error.
- The last run shows citations, explanations, raw model JSON, and status.
- Manual edits are kept. Export Word (`.docx`) from the official template, or Markdown (full, answered only, or not in specification).

## Sign-in and secrets

Sign-in uses Auth0 through Streamlit. Email must be verified, and the address must be on an allowed domain (`hayselectrical.com`).

Secrets stay out of git. Locally they live in `.streamlit/secrets.toml`. On Streamlit Community Cloud, paste the same file in the app's **Settings → Secrets**.

Required names:

- `OPENAI_API_KEY` on the first line, above every `[section]`
- `[auth]` with `redirect_uri` and `cookie_secret`
- `[auth.auth0]` with `domain`, `client_id`, `client_secret`, `server_metadata_url`, `ALLOWED_DOMAINS`, and `ALLOWED_EMAILS`
- `[neon_db]` with `user`, `password`, `host`, `database`, `port`, `TABLE_NAME`, and `CHAT_THREADS_TABLE_NAME`

`redirect_uri` is different in each place:

- Local: `http://localhost:8501/oauth2callback`
- Cloud: `https://YOUR-APP.streamlit.app/~/+/oauth2callback`

Auth0 must list both callback URLs, and both origins as Allowed Logout URLs and Allowed Web Origins.

## Run locally

```
cd "C:\Users\SeshaKalyanam\OneDrive - HAYS ELECTRICAL SERVICES\Documents\developer projects\specs - main\hays-specs-main\hays-specs-main"
venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

The live app deploys from [github.com/seshasai-kalyanam/Hays-AI-Spec-Assistant](https://github.com/seshasai-kalyanam/Hays-AI-Spec-Assistant), branch `main`, main file `app.py`. A push to `main` rebuilds the Streamlit app. Do not commit `.streamlit/secrets.toml`.

## Offline QA scripts

`qa/` and `qa_checklist/` are older test scripts. They still call Gemini and OpenRouter and are not used by the deployed app. The running app does not need `GEMINI_API_KEY` or `OPENROUTER_API_KEY`.
