"""Pytest: exported DOCX must match MD fixture for answered fields."""

import tempfile
import unittest
from pathlib import Path

from tests.docx.md_docx_compare import (
    ANSWERED_LINE_RE,
    discover_docx_fixture,
    discover_md_fixture,
    format_report,
    parse_md_answers,
    run_compare,
)
from utils.checklist_schema import attach_docx_refs, load_prompts_csv
from utils.config_vars import CHECKLIST_PROMPTS_CSV, CHECKLIST_TEMPLATE_DOCX
from utils.results_form import _resolve_option_column, export_checklist_docx

FIXTURE_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURE_DIR.parent.parent


class MdDocxFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md_path = discover_md_fixture(FIXTURE_DIR)
        cls.docx_path = discover_docx_fixture(FIXTURE_DIR)
        cls.fields = attach_docx_refs(
            load_prompts_csv(CHECKLIST_PROMPTS_CSV),
            REPO_ROOT / CHECKLIST_TEMPLATE_DOCX,
        )

    def test_fixture_paths_are_expected(self):
        self.assertEqual(self.md_path.name, "spec_compliance_checklist.md")
        self.assertEqual(self.docx_path.name, "spec_compliance_checklist.docx")

    def test_committed_docx_matches_md(self):
        """Committed Word export must match MD fixture for answered prompts."""
        mismatches = run_compare(self.md_path, self.docx_path, self.fields)
        if mismatches:
            report = format_report(mismatches, self.md_path, self.docx_path)
            self.fail(report)

    def test_parse_md_captures_prompt_number_and_label(self):
        sample = "- [x] **21.** **Indoors:** THHN/THWN"
        match = ANSWERED_LINE_RE.match(sample.strip())
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "21")
        self.assertEqual(match.group(2), "Indoors")
        self.assertEqual(match.group(3), "THHN/THWN")

    def test_all_choice_answers_resolve_to_docx_columns(self):
        """Every allowed choice option must have a place in the Word template."""
        missing = []
        for field in self.fields:
            if field.field_kind != "choice" or not field.allowed_options:
                continue
            if field.docx_ref is None:
                missing.append(f"{field.prompt_number}. {field.display_label}: no docx_ref")
                continue
            for option in field.allowed_options:
                if _resolve_option_column(field.docx_ref, option) is None:
                    missing.append(
                        f"{field.prompt_number}. {field.display_label}: {option}"
                    )

        self.assertEqual([], missing)

    def _values_from_md(self) -> dict[int, str]:
        md_answers = parse_md_answers(self.md_path)
        return {
            f.row_index: md_answers[f.prompt_number]
            for f in self.fields
            if f.prompt_number in md_answers
        }

    def test_exported_docx_matches_md(self):
        """Export from MD answers (app path) and assert DOCX matches MD truth."""
        values = self._values_from_md()
        out = export_checklist_docx(self.fields, values)
        self.assertIsNotNone(out)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp_path.write_bytes(out)
        try:
            mismatches = run_compare(self.md_path, tmp_path, self.fields)
            if mismatches:
                report = format_report(mismatches, self.md_path, tmp_path)
                self.fail(report)
        finally:
            tmp_path.unlink(missing_ok=True)
