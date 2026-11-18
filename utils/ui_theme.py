"""Optional layout/CSS tweaks for a cleaner, ChatGPT-style chat surface."""

import streamlit as st

GPT_STYLE_MARKDOWN = r"""
<style>
  /* Main padding: keep below Streamlit’s top chrome so first row (logo/title) is not clipped */
  .block-container {
    padding-top: 3rem;
    padding-bottom: 1rem;
  }
  /* Main-area title row: flex centering (replaces st.columns + st.image, which could clip the logo) */
  .hays-app-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0 0.1rem 0;
    margin: 0;
    overflow: visible;
  }
  .hays-app-header img {
    width: 56px;
    height: auto;
    flex-shrink: 0;
    display: block;
  }
  .hays-app-header-text {
    min-width: 0;
  }
  .hays-app-header h3 {
    margin: 0;
    padding: 0;
    font-size: 1.375rem;
    font-weight: 600;
    line-height: 1.35;
    color: rgb(49, 51, 63);
    overflow: visible;
  }
  /* Prevent Streamlit markdown wrapper from clipping custom header ascenders */
  [data-testid="stElementContainer"]:has(.hays-app-header),
  [data-testid="stMarkdownContainer"]:has(.hays-app-header) {
    overflow: visible !important;
  }
  [data-testid="stCaptionContainer"] {
    margin-top: 0;
    margin-bottom: 0.05rem;
  }
  /* Main chat column: tighter block gaps */
  [data-testid="stMainBlockContainer"] [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {
    gap: 0.3rem;
  }
  [data-testid="stMainBlockContainer"] [data-testid="stElementContainer"] {
    margin-bottom: 0.1rem;
  }
  /* File uploader: compact when PDFs are attached */
  [data-testid="stFileUploader"] [data-testid="stFileUploaderDropzone"] {
    padding: 0.25rem 0.5rem;
    min-height: 0;
  }
  [data-testid="stFileUploader"] [data-testid="stFileUploaderFile"] {
    margin-bottom: 0;
  }
  /* Chat messages */
  [data-testid="stChatMessage"] {
    padding-top: 0.25rem;
    padding-bottom: 0.25rem;
    gap: 0.35rem;
  }
  [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
    margin-bottom: 0.35rem;
  }
  [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p:last-child {
    margin-bottom: 0;
  }
  section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f8f8f8 0%, #f4f4f4 100%);
  }
  section[data-testid="stSidebar"] [data-baseweb="block"] {
    font-size: 0.9rem;
  }
  /* Chat input: softer bar */
  [data-testid="stChatInput"] {
    border-radius: 1.25rem !important;
  }
  [data-testid="stChatInput"] textarea,
  [data-testid="stChatInput"] input {
    border-radius: 1.25rem !important;
  }
  /* Recents thread titles: link-like (tertiary); feedback uses main area, not sidebar */
  section[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"] {
    min-height: 2.1rem;
    font-weight: 400;
    color: #0b57d0;
    text-align: left;
    justify-content: flex-start;
  }
  section[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"]:hover:not(:disabled) {
    text-decoration: underline;
  }
  section[data-testid="stSidebar"] [data-testid="stBaseButton-tertiary"]:disabled {
    color: #9e9e9e;
  }
  /* Recents list scroll: st.container(key=hays_recents_scroll, height=288) in app; padding for scrollbar */
  section[data-testid="stSidebar"] [class*='st-key-hays_recents_scroll'] {
    padding-right: 0.1rem;
  }
  /* Standalone use (e.g. future HTML) */
  .hays-recents-scroll {
    max-height: min(42vh, 18rem);
    overflow-y: auto;
    padding-right: 0.1rem;
  }
  .hays-pill-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.4rem 0.5rem;
    margin: 0.9rem 0 0.25rem 0;
  }
  /* LLM Sources block: keep rows tight inside chat messages */
  [class*='st-key-hays_citation_sources'] [data-testid='stElementContainer'] {
    margin-bottom: 0 !important;
  }
  [class*='st-key-hays_citation_sources'] p {
    margin: 0 !important;
    line-height: 1.35;
  }
  [class*='st-key-hays_citation_sources'] [data-testid='stBaseButton-secondary'] {
    min-height: 1.75rem;
    padding-top: 0.15rem;
    padding-bottom: 0.15rem;
    white-space: nowrap;
  }
  [class*='st-key-hays_page_nav'] p {
    white-space: nowrap;
    margin: 0;
  }
  [class*='st-key-hays_workspace'] [data-testid='stColumn'] {
    min-width: 0;
  }
  @media (max-width: 1100px) {
    [class*='st-key-hays_workspace'] > div [data-testid='stHorizontalBlock'] {
      flex-wrap: wrap;
    }
  }
</style>
"""


def inject_gpt_inspired_layout_css() -> None:
    st.markdown(GPT_STYLE_MARKDOWN, unsafe_allow_html=True)
