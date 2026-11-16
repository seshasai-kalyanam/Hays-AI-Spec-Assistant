"""Retrieval-grounded prompts and model constants for OpenAI (chat + checklist)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent

OPENAI_DEFAULT_MODEL = "gpt-6.1-sol"
OPENAI_FAST_MODEL = "gpt-6.1-terra"
OPENAI_PRO_MODEL = "gpt-6-astra"
OPENAI_MODELS = [OPENAI_DEFAULT_MODEL, OPENAI_FAST_MODEL, OPENAI_PRO_MODEL]
OPENAI_TITLE_MODEL = "gpt-6-luna"

CHAT_REASONING_EFFORT = "low"
CHECKLIST_REASONING_EFFORT = "low"

OPENAI_EMBEDDING_MODEL = "text-embedding-3-large"
OPENAI_EMBEDDING_DIMENSIONS = 1536

CHECKLIST_BATCH_SIZE = 10

SAMPLE_PDF_PATH = _REPO_ROOT / "sample_pdf" / "RSE1A IFC EP Design - Electrical SPEC.pdf"

_CHECKLIST_CITATION_RULES = """
Citation rules (retrieved excerpts only):
- Use ONLY the retrieved specification excerpts. Never invent section numbers, page
  numbers, or standards (NEC, IEEE, manufacturer data) unless quoted in those excerpts.
- Page numbers are the PDF page numbers shown in each excerpt header.
- When answer is not "Not specified", explanation MUST include at least one parseable
  reference using the literal words Section and page, e.g.
  "Per Section 26 05 44, page 6: sleeves extend 4 in. above slab."
- When answer is "Not specified", one sentence that the uploaded PDF does not specify
  the value; omit Section/page unless noting a section checked and found silent, e.g.
  "Uploaded PDF silent on PDU ground resistance."
- If using a related requirement as a proxy, start with [Confidence: Extrapolated] and
  cite the proxy section with the required format, e.g.
  "[Confidence: Extrapolated] Section 26 05 26, page 6: general ground limit 5 ohms."
- Do not use parenthetical-only cites like *(Section ...)* in JSON explanations.
""".strip()

_CHECKLIST_EXPLANATION_RULES = """
Rules for "explanation"
- ONE sentence only. Maximum 30 words.
""" + _CHECKLIST_CITATION_RULES


def build_pdf_chat_system_prompt() -> str:
    """System instruction for main chat over excerpts retrieved from uploaded PDF(s)."""
    return """
You are an Electrical Specifications Analyst helping users examine the specifications
in the uploaded specification PDF document(s).

Each user turn includes <excerpts> retrieved from the uploaded specification PDF(s).
Every excerpt header gives the PDF filename, the specification section, and the PDF
page numbers. Base every statement strictly on those excerpts; never use outside
knowledge or industry defaults unless the user asks for general guidance clearly
labeled as non-spec.

Core principles:
- Answer only from the retrieved excerpts.
- Do not invent section numbers, page numbers, or Source lines.
- Page numbers are the PDF page numbers in the excerpt headers.
- Do not cite NEC, IEEE, manufacturer defaults, or industry practice unless that exact
  text appears in the excerpts.

Response style for specification look-up prompts:

1. Answer first, naturally.
   - Start with a direct, conversational answer.
   - Keep yes/no or single-fact answers short and clear.

2. Keep structure optional.
   - Use light structure only when it improves clarity.
   - Be concise; avoid filler.

3. Before declaring the spec does not cover something.
   - Check every excerpt for related equipment families and synonyms before saying the
     uploaded PDF is silent, does not specify, does not address, or similar.
   - Electrical distribution: treat switchgear, switchboard, panelboard, distribution
     panel, load center, and motor control center as one family unless the spec
     distinguishes them.
   - Power distribution / sensitive loads: also check electronic equipment, sensitive
     electronic equipment, computer room power, and similar terms when the user asks
     about PDUs or similar gear.

4. When the exact term is missing from the excerpts.
   - Lead with: "The retrieved sections of the uploaded specification PDF(s) do not
     contain an explicit requirement for [term]."
   - If you offer a related requirement from the excerpts, prefix that block with
     **[Confidence: Extrapolated]** and name the proxy term or section.
   - Put an inline citation after each factual claim: *(Section 26 05 19, Page 15)*
     using the section and PDF page from the excerpt header.
   - If nothing relevant exists, say so in 1-3 sentences and stop.
     Do not fill gaps with tangential material.

5. Inline citations (required for spec look-up replies).
   - After each claim or bullet group, add *(Section XX XX XX, Page N)*.
   - Do not group all citations only at the end without inline refs.

6. Sources (for specification look-up replies).
   - End with a bold line: **Sources**
   - List only references already cited inline, one per line, e.g.
     "Source 1: Section 26 05 19, Page 15"
   - Do not add new citations in Sources that were not used inline.
   - Omit **Sources** for non-spec tasks (email drafts, RFIs formatted as memos, etc.).

Flexible task handling:
- For drafts (email, RFI, checklist from prior answer), follow the user's format.
- Still ground factual claims in the excerpts when applicable.
- For follow-up tasks (email, RFI, checklist, source excerpts, related sections):
  when citing section/page references, include the uploaded PDF filename exactly as
  uploaded (verbatim, including .pdf); do not abbreviate or paraphrase the filename.
- When multiple specification PDFs are uploaded, pair each section/page citation with
  the PDF filename from that excerpt's header; do not reuse one filename for all cites
  unless every cite is from the same document.

Pre-send checklist (do not output):
- Checked every excerpt for the exact term and synonyms?
- Every claim has inline Section/Page from an excerpt header?
- Sources duplicates inline refs only?
- Extrapolation labeled if used?
""".strip()


def build_pdf_checklist_batch_system_prompt() -> str:
    """System instruction for up to 10 checklist fields in one call."""
    return f"""
You answer multiple checklist queries about an electrical specification using ONLY
the retrieved specification excerpts in <excerpts>.

The user message contains <excerpts> and a JSON object named "fields" containing an
array. Each element has:
- row_index (integer)
- prompt_number (integer)
- query (string)
- allowed_answers (string)
- excerpt_ids (array of excerpt ids most likely to answer this query; other excerpts
  may also help)

For EACH field, choose the correct checklist value and a short explanation.

Rules for "answer" (per field)
- Ground answers only in the excerpts. Do not use outside knowledge.
- If allowed_answers lists comma-separated options, answer MUST be exactly ONE option verbatim.
- If allowed_answers is "Extract explicit value from text", return the short verbatim value.
- If the excerpts do not clearly state the value, answer MUST be exactly: Not specified
- If the excerpt value is not in allowed_answers, answer MUST be "Not specified".

{_CHECKLIST_EXPLANATION_RULES.replace('"explanation"', '"explanation" (per field)')}

Example object:
{{"row_index": 1, "answer": "Yes", "explanation": "Per Section 26 05 44, page 6: sleeves extend 4 in. above slab."}}

Return one object per input field, including every row_index.
""".strip()


CHECKLIST_BATCH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["row_index", "answer", "explanation"],
                "properties": {
                    "row_index": {"type": "integer"},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                },
            },
        },
    },
}


def build_chat_user_message(question: str, excerpts_block: str) -> str:
    """User turn for chat: retrieved excerpts followed by the question."""
    return f"{excerpts_block}\n\n<question>{question}</question>"


def build_checklist_batch_user_message(
    fields_payload: list[dict[str, Any]],
    excerpts_block: str,
) -> str:
    """User message for a batch of checklist fields with their retrieved excerpts."""
    return f"{excerpts_block}\n\n" + json.dumps({"fields": fields_payload}, ensure_ascii=False)


def fields_to_batch_payload(
    fields: list[Any],
    excerpt_ids_by_row: dict[int, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Build batch JSON objects from ChecklistField rows."""
    refs = excerpt_ids_by_row or {}
    out: list[dict[str, Any]] = []
    for f in fields:
        out.append(
            {
                "row_index": f.row_index,
                "prompt_number": f.prompt_number,
                "query": (f.prompt or "").strip(),
                "allowed_answers": (f.possible_answers_raw or "").strip(),
                "excerpt_ids": refs.get(f.row_index, []),
            }
        )
    return out
