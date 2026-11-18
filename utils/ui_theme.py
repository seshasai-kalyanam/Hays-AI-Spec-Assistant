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
    min-height: 56px;
    padding: 0 0 0.2rem 0;
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
    flex: 0 0 auto !important;
    height: auto !important;
    min-height: 4.1rem !important;
    margin-bottom: 0.35rem !important;
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
  /* Both panes fill the window. Answers scroll inside the left pane. The PDF fills the right pane. */
  [data-testid='stMainBlockContainer']:has([class*='st-key-hays_workspace']) {
    height: 100vh;
    max-height: 100vh;
    overflow: hidden;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    padding-top: 2.4rem;
    padding-bottom: 0.2rem;
  }
  [data-testid='stMainBlockContainer']:has([class*='st-key-hays_workspace']) > div,
  [data-testid='stLayoutWrapper']:has([class*='st-key-hays_workspace']) {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    height: 100% !important;
    display: flex !important;
    flex-direction: column !important;
  }
  [class*='st-key-hays_workspace'] {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    height: 100% !important;
  }
  [class*='st-key-hays_workspace'] [data-testid='stHorizontalBlock'] {
    flex: 1 1 auto;
    height: 100%;
    min-height: 0;
    align-items: stretch;
  }
  [class*='st-key-hays_workspace'] [data-testid='stColumn'],
  [class*='st-key-hays_workspace'] [data-testid='stColumn'] > [data-testid='stVerticalBlock'] {
    height: 100%;
    min-height: 0;
  }
  [data-testid='stLayoutWrapper']:has([class*='st-key-hays_chat_column']) {
    flex: 1 1 auto !important;
    height: 100% !important;
    min-height: 0 !important;
    display: flex !important;
    flex-direction: column !important;
  }
  [class*='st-key-hays_chat_column'] {
    flex: 1 1 auto !important;
    height: 100% !important;
    min-height: 0 !important;
    display: flex !important;
    flex-direction: column !important;
  }
  [class*='st-key-hays_chat_transcript'] {
    padding-right: 0.35rem;
  }
  [class*='st-key-clear_chat_inline'] button {
    white-space: nowrap;
  }
  [class*='st-key-hays_chat_column'] [data-testid='stElementContainer']:has([data-testid='stFileUploader']),
  [class*='st-key-hays_chat_column'] [data-testid='stElementContainer']:has([data-testid='stChatInput']) {
    flex: 0 0 auto !important;
  }
  [data-testid='stLayoutWrapper']:has([class*='st-key-hays_chat_transcript']) {
    flex: 1 1 auto !important;
    height: auto !important;
    min-height: 0 !important;
    max-height: none !important;
    display: flex !important;
    flex-direction: column !important;
  }
  [class*='st-key-hays_chat_transcript'] {
    flex: 1 1 auto !important;
    min-height: 0 !important;
    height: auto !important;
    max-height: none !important;
    overflow-y: auto !important;
  }
  [data-testid='stLayoutWrapper']:has([class*='st-key-hays_source_panel']) {
    flex: 1 1 auto !important;
    height: 100% !important;
    min-height: 0 !important;
    max-height: none !important;
    display: flex !important;
    flex-direction: column !important;
  }
  [class*='st-key-hays_source_panel'] {
    flex: 1 1 auto !important;
    height: 100% !important;
    min-height: 0 !important;
    max-height: 100% !important;
    display: flex !important;
    flex-direction: column !important;
    box-sizing: border-box;
    border: 1px solid rgba(49, 51, 63, 0.16);
    border-radius: 0.7rem;
    padding: 0.45rem 0.7rem 0.55rem;
    background: #fff;
    overflow-y: auto !important;
    overflow-x: hidden !important;
  }
  @media (max-width: 1100px) {
    [data-testid='stMainBlockContainer']:has([class*='st-key-hays_workspace']) {
      height: auto;
      max-height: none;
      overflow: visible;
    }
    [class*='st-key-hays_workspace'] [data-testid='stHorizontalBlock'] {
      flex-wrap: wrap;
      height: auto;
    }
    [data-testid='stLayoutWrapper']:has([class*='st-key-hays_chat_transcript']),
    [class*='st-key-hays_chat_transcript'],
    [data-testid='stLayoutWrapper']:has([class*='st-key-hays_source_panel']),
    [class*='st-key-hays_source_panel'] {
      height: 70vh !important;
      max-height: 70vh !important;
    }
  }
</style>
"""


def inject_gpt_inspired_layout_css() -> None:
    st.markdown(GPT_STYLE_MARKDOWN, unsafe_allow_html=True)
