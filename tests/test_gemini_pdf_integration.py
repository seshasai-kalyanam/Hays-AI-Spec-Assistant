"""Integration tests: Gemini + specification PDFs (small and large)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from utils.checklist_answer_explainer import parse_answer_explanation
from utils.checklist_schema import ChecklistField, attach_docx_refs, load_prompts_csv
from utils.config_vars import CHECKLIST_PROMPTS_CSV, CHECKLIST_TEMPLATE_DOCX
from utils.gemini_client import (
    chat_with_pdf,
    generate_checklist_batch,
    generate_checklist_single,
    get_genai_client,
    parse_checklist_batch_response,
    upload_pdf_path,
)
from utils.gemini_prompts import (
    GEMINI_DEFAULT_MODEL,
    build_pdf_chat_system_prompt,
    fields_to_batch_payload,
)
from utils.pdf_split import split_pdf_for_gemini
from utils.results_form import NOT_SPECIFIED, run_checklist_batch_autofill
from utils.streamlit_secrets import get_secret

_CURATED_CSV = Path(__file__).resolve().parent.parent / "qa_checklist" / "prompts_curated.csv"
_TESTS_DIR = Path(__file__).resolve().parent
_SMALL_TEST_PDF = _TESTS_DIR / "RSE1A IFC EP Design - Electrical SPEC.pdf"
_LARGE_TEST_PDF = _TESTS_DIR / "2026-03-20 PACKAGE 9 - PERMIT SET - SPECIFICATIONS_electrical.pdf"
_SPLIT_CACHE_DIR = _TESTS_DIR / ".pdf_parts"

_INTEGRATION_PDF_CASES = [
    (_SMALL_TEST_PDF, 1),
    (_LARGE_TEST_PDF, 2),
]

INTEGRATION_PDFS = [
    pytest.param(_SMALL_TEST_PDF, 1, id="rse1a-small"),
    pytest.param(_LARGE_TEST_PDF, 2, id="permit-large"),
]

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def gemini_api_key():
    key = get_secret("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        pytest.skip(
            "GEMINI_API_KEY not in .streamlit/secrets.toml (see KEYS_FROM_SECRETS.md)"
        )
    return str(key).strip()


@pytest.fixture(scope="session")
def gemini_client(gemini_api_key):
    return get_genai_client(gemini_api_key)


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_pdf_exists(pdf_path: Path, expected_parts: int):
    assert pdf_path.is_file(), f"Missing integration PDF: {pdf_path}"


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_split_pdf_part_count(pdf_path: Path, expected_parts: int):
    cache_dir = _SPLIT_CACHE_DIR / pdf_path.stem
    parts = split_pdf_for_gemini(pdf_path, cache_dir)
    if expected_parts == 1:
        assert len(parts) == 1
        assert parts[0] == pdf_path
    else:
        assert len(parts) >= expected_parts
        for part in parts:
            assert part.stat().st_size <= 45 * 1024 * 1024


@pytest.fixture(scope="session")
def uploaded_pdf_sets(gemini_client):
    """Upload both PDF fixtures once; return {stem: [gemini_file, ...]}."""
    sets: dict[str, list] = {}
    for pdf_path, _ in _INTEGRATION_PDF_CASES:
        cache_dir = _SPLIT_CACHE_DIR / pdf_path.stem
        local_paths = split_pdf_for_gemini(pdf_path, cache_dir)
        sets[pdf_path.stem] = [
            upload_pdf_path(gemini_client, path) for path in local_paths
        ]
    return sets


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_upload_sample_pdf_active(
    pdf_path: Path, expected_parts: int, uploaded_pdf_sets: dict
):
    files = uploaded_pdf_sets[pdf_path.stem]
    assert len(files) == expected_parts or (expected_parts > 1 and len(files) >= expected_parts)
    for gemini_file in files:
        assert gemini_file.state.name == "ACTIVE"


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_chat_answers_from_pdf(
    pdf_path: Path, expected_parts: int, gemini_client, uploaded_pdf_sets: dict
):
    files = uploaded_pdf_sets[pdf_path.stem]
    system = build_pdf_chat_system_prompt()
    answer = chat_with_pdf(
        gemini_client,
        GEMINI_DEFAULT_MODEL,
        system,
        files,
        "Is EMT utilization allowed?",
    )
    assert answer
    assert len(answer) > 40
    lowered = answer.lower()
    assert "don't have access" not in lowered
    assert "cannot access" not in lowered


def _load_field_by_prompt_number(prompt_number: int) -> ChecklistField:
    fields = attach_docx_refs(
        load_prompts_csv(CHECKLIST_PROMPTS_CSV),
        CHECKLIST_TEMPLATE_DOCX,
    )
    for f in fields:
        if f.prompt_number == prompt_number:
            return f
    raise ValueError(f"No field with prompt_number={prompt_number}")


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_checklist_single_field_json(
    pdf_path: Path, expected_parts: int, gemini_client, uploaded_pdf_sets: dict
):
    files = uploaded_pdf_sets[pdf_path.stem]
    field = _load_field_by_prompt_number(3)
    raw = generate_checklist_single(
        gemini_client,
        GEMINI_DEFAULT_MODEL,
        files,
        field.prompt,
        field.possible_answers_raw,
    )
    parsed, explanation, parse_valid, _note = parse_answer_explanation(raw, field)
    assert raw
    assert parsed
    if field.allowed_options:
        assert parsed in field.allowed_options or parsed == NOT_SPECIFIED
    assert explanation or parsed == NOT_SPECIFIED


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_checklist_batch_ten_json(
    pdf_path: Path, expected_parts: int, gemini_client, uploaded_pdf_sets: dict
):
    files = uploaded_pdf_sets[pdf_path.stem]
    fields = attach_docx_refs(
        load_prompts_csv(CHECKLIST_PROMPTS_CSV),
        CHECKLIST_TEMPLATE_DOCX,
    )[:10]
    payload = fields_to_batch_payload(fields)
    raw = generate_checklist_batch(
        gemini_client,
        GEMINI_DEFAULT_MODEL,
        files,
        payload,
    )
    rows = parse_checklist_batch_response(raw)
    assert len(rows) == 10
    indices = {r.get("row_index") for r in rows}
    expected = {f.row_index for f in fields}
    assert indices == expected
    for row in rows:
        assert isinstance(row.get("answer"), str) and row["answer"].strip()


@pytest.mark.parametrize("pdf_path,expected_parts", INTEGRATION_PDFS)
def test_checklist_batch_curated_csv(
    pdf_path: Path, expected_parts: int, uploaded_pdf_sets: dict
):
    files = uploaded_pdf_sets[pdf_path.stem]
    fields = load_prompts_csv(_CURATED_CSV)[:10]
    assert len(fields) == 10
    results = run_checklist_batch_autofill(
        fields,
        files,
        model=GEMINI_DEFAULT_MODEL,
    )
    assert len(results) == 10
    for result in results:
        assert "parsed_answer" in result
        assert "parse_valid" in result
        assert isinstance(result.get("parsed_answer"), str)
