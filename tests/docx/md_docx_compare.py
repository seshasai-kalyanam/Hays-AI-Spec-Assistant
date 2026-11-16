"""Compare exported checklist Markdown (ground truth) to exported DOCX."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from docx import Document
from docx.oxml.ns import qn

from utils.checklist_schema import (
    ChecklistField,
    DocxRef,
    attach_docx_refs,
    is_embedded_subfield_ref,
    load_prompts_csv,
)
from utils.config_vars import CHECKLIST_PROMPTS_CSV, CHECKLIST_TEMPLATE_DOCX
from utils.results_form import (
    DOCX_NOTES_EXPORT_PREFIX_RE,
    _normalize_option_label,
)

FOOTNOTES_HEADER = "## Footnotes and citations"
FIXTURE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE_STEM = "spec_compliance_checklist"

ANSWERED_LINE_RE = re.compile(
    r"^- \[[x ]\] (?:\*\*(\d+)\.\*\* )?\*\*(.+?):\*\* (.+)$",
    re.IGNORECASE,
)


@dataclass
class FieldMismatch:
    category: str
    label: str
    kind: str
    md_value: str
    docx_mapped: str | None
    detail: str = ""
    prompt_number: int | None = None

    def format_lines(self) -> list[str]:
        def _one_line(val: str | None) -> str:
            if not val:
                return "(none)"
            return " ".join(val.split())

        prompt_tag = f" #{self.prompt_number}" if self.prompt_number is not None else ""
        lines = [
            f"{self.kind.upper()}: {self.category} / {self.label}{prompt_tag}",
            f"  MD:            {self.md_value}",
            f"  DOCX (mapped): {_one_line(self.docx_mapped)}",
        ]
        if self.detail:
            lines.append(f"  {self.detail}")
        return lines


def parse_md_answers(md_path: str | Path) -> dict[int, str]:
    """Parse answered checklist lines from exported markdown, keyed by prompt number."""
    path = Path(md_path)
    answers: dict[int, str] = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == FOOTNOTES_HEADER:
            break
        ans_match = ANSWERED_LINE_RE.match(line)
        if not ans_match or not ans_match.group(1):
            continue
        value = ans_match.group(3).strip()
        if value and value.lower() != "_unanswered_":
            answers[int(ans_match.group(1))] = value
    return answers


def _paragraph_text(element) -> str:
    return "".join(t.text or "" for t in element.iter() if t.tag.endswith("}t"))


def _paragraph_matches_option(paragraph, option: str) -> bool:
    para = _normalize_option_label(_paragraph_text(paragraph))
    opt = _normalize_option_label(option)
    if not para or not opt:
        return False
    return para == opt


def _paragraph_has_checked_checkbox(paragraph) -> bool:
    checkbox = paragraph.find(qn("w:checkBox"))
    if checkbox is None:
        checkbox = paragraph.find(f".//{qn('w:checkBox')}")
    if checkbox is None:
        return False
    for tag in ("w:checked", "w:default"):
        child = checkbox.find(qn(tag))
        if child is not None and child.get(qn("w:val")) == "1":
            return True
    return False


def _option_marked_in_cell(cell, option: str) -> bool:
    """True only when the option's checkbox is checked, or a legacy X marks that option."""
    opt_norm = _normalize_option_label(option)
    checkbox_paragraphs = [
        p
        for p in cell._tc.iter(qn("w:p"))
        if p.find(f".//{qn('w:checkBox')}") is not None
    ]
    if checkbox_paragraphs:
        for paragraph in checkbox_paragraphs:
            if not _paragraph_matches_option(paragraph, option):
                continue
            if _paragraph_has_checked_checkbox(paragraph):
                return True
        return False

    text = cell.text.strip()
    if not text:
        return False
    norm = _normalize_option_label(text)
    if text.upper() == "X" and norm == opt_norm:
        return True
    return norm == opt_norm


def _normalize_choice_read(opt: str, allowed_options: list[str] | None) -> str:
    text = " ".join(opt.split())
    if not allowed_options:
        return text
    lower = text.lower()
    for candidate in allowed_options:
        if candidate.lower() == lower:
            return candidate
    return text


def _read_choice_from_ref(
    table,
    docx_ref: DocxRef,
    allowed_options: list[str] | None = None,
) -> str | None:
    if docx_ref.row_index >= len(table.rows):
        return None
    row = table.rows[docx_ref.row_index]
    selected: list[str] = []
    seen_cols: set[int] = set()

    for opt, col in docx_ref.option_col_map.items():
        if col >= len(row.cells) or col in seen_cols:
            continue
        seen_cols.add(col)
        cell = row.cells[col]
        if _option_marked_in_cell(cell, opt):
            selected.append(_normalize_choice_read(opt, allowed_options))

    if selected:
        unique: list[str] = []
        seen_opts: set[str] = set()
        for opt in selected:
            key = opt.lower()
            if key not in seen_opts:
                seen_opts.add(key)
                unique.append(opt)
        if len(unique) == 1:
            return unique[0]
        if allowed_options:
            for candidate in allowed_options:
                if candidate in unique:
                    return candidate
        return "|".join(unique)

    if allowed_options:
        seen_cols = set()
        for opt in allowed_options:
            col = docx_ref.option_col_map.get(opt)
            if col is None:
                for key, ci in docx_ref.option_col_map.items():
                    if key.lower() == opt.lower():
                        col = ci
                        break
            if col is None or col in seen_cols or col >= len(row.cells):
                continue
            seen_cols.add(col)
            cell = row.cells[col]
            if _option_marked_in_cell(cell, opt):
                return _normalize_choice_read(opt, allowed_options)

    for opt, col in docx_ref.option_col_map.items():
        if col >= len(row.cells) or col in seen_cols:
            continue
        seen_cols.add(col)
        if _option_marked_in_cell(row.cells[col], opt):
            selected.append(_normalize_choice_read(opt, allowed_options))

    options_to_scan = allowed_options or list(docx_ref.option_col_map.keys())
    for ci, cell in enumerate(row.cells):
        if ci == 0 or ci in seen_cols:
            continue
        text = " ".join(cell.text.split())
        if not text:
            continue
        for opt in options_to_scan:
            if opt in selected:
                continue
            if _option_marked_in_cell(cell, opt):
                selected.append(_normalize_choice_read(opt, allowed_options))
                break

    if not selected:
        return None
    if len(selected) > 1:
        return "|".join(selected)
    return selected[0]


def _read_text_from_ref(table, docx_ref: DocxRef) -> str | None:
    if docx_ref.row_index >= len(table.rows):
        return None
    row = table.rows[docx_ref.row_index]
    col = docx_ref.value_col_index if docx_ref.value_col_index is not None else 1
    if col < len(row.cells):
        text = row.cells[col].text.strip()
        return text or None
    return None


def _read_embedded_subfield_from_ref(
    table,
    docx_ref: DocxRef,
    field: ChecklistField,
) -> str | None:
    """Read answer from embedded sub-column text (e.g. '10. Duration: 2 hours')."""
    text = _read_text_from_ref(table, docx_ref)
    if not text:
        return None
    label = re.escape(field.display_label.rstrip(":"))
    prefix_re = re.compile(
        rf"^\s*(?:\d+|[A-Z]+)\.\s*{label}\s*:\s*",
        re.IGNORECASE,
    )
    answer = prefix_re.sub("", text, count=1).strip()
    return answer or None


def _read_notes_from_ref(table, docx_ref: DocxRef) -> str | None:
    """Read answer text from a merged Additional Information/Notes row."""
    if docx_ref.row_index >= len(table.rows):
        return None
    row = table.rows[docx_ref.row_index]
    if not row.cells:
        return None
    text = row.cells[0].text.strip()
    if not text:
        return None
    answer = DOCX_NOTES_EXPORT_PREFIX_RE.sub("", text, count=1).strip()
    return answer or None


def _read_field_value(doc: Document, field: ChecklistField, ref: DocxRef) -> str | None:
    if ref.table_index >= len(doc.tables):
        return None
    table = doc.tables[ref.table_index]
    if field.field_kind == "choice":
        return _read_choice_from_ref(table, ref, field.allowed_options or None)
    if field.field_kind == "notes":
        return _read_notes_from_ref(table, ref)
    if is_embedded_subfield_ref(ref):
        return _read_embedded_subfield_from_ref(table, ref, field)
    return _read_text_from_ref(table, ref)


def _normalize_value(value: str, field: ChecklistField | None) -> str:
    text = value.strip()
    if field and field.field_kind == "choice" and field.allowed_options:
        lower = text.lower()
        for opt in field.allowed_options:
            if opt.lower() == lower:
                return opt
    return text


def _values_match(md_value: str, docx_value: str | None, field: ChecklistField | None) -> bool:
    if docx_value is None:
        return False
    md_norm = _normalize_value(md_value, field)
    docx_norm = _normalize_value(docx_value, field)
    if md_norm.lower() == docx_norm.lower():
        return True
    if field and field.field_kind == "choice":
        parts = {p.strip().lower() for p in re.split(r"[\s|]+", docx_norm) if p.strip()}
        if md_norm.lower() in parts:
            return True
        if field.allowed_options and md_norm.lower() in docx_norm.lower():
            matching = [o for o in field.allowed_options if o.lower() in docx_norm.lower()]
            if len(matching) == 1 and matching[0].lower() == md_norm.lower():
                return True
            if md_norm.lower() in {m.lower() for m in matching}:
                for token in parts:
                    if token == md_norm.lower():
                        return True
    return False


def compare_answers(
    md_answers: dict[int, str],
    docx_path: str | Path,
    fields: list[ChecklistField],
) -> list[FieldMismatch]:
    doc = Document(str(docx_path))
    by_prompt = {f.prompt_number: f for f in fields}
    mismatches: list[FieldMismatch] = []

    for prompt_number, md_value in sorted(md_answers.items()):
        field = by_prompt.get(prompt_number)
        if field is None:
            mismatches.append(
                FieldMismatch(
                    category="",
                    label="",
                    kind="unknown_field",
                    md_value=md_value,
                    docx_mapped=None,
                    detail=f"No CSV field for prompt #{prompt_number}.",
                    prompt_number=prompt_number,
                )
            )
            continue

        category = field.category
        label = field.display_label
        docx_val = (
            _read_field_value(doc, field, field.docx_ref)
            if field.docx_ref is not None
            else None
        )

        if field.docx_ref is None:
            mismatches.append(
                FieldMismatch(
                    category=category,
                    label=label,
                    kind="mapping_missing",
                    md_value=md_value,
                    docx_mapped=docx_val,
                    detail="Field has no docx_ref from template mapping.",
                    prompt_number=prompt_number,
                )
            )
            continue

        ref = field.docx_ref
        location = (
            f"table={ref.table_index} row={ref.row_index} "
            f"options={list(ref.option_col_map.keys()) if ref.option_col_map else 'text'}"
        )

        if _values_match(md_value, docx_val, field):
            continue

        kind = "docx_unanswered" if not docx_val else "mismatch"
        mismatches.append(
            FieldMismatch(
                category=category,
                label=label,
                kind=kind,
                md_value=md_value,
                docx_mapped=docx_val,
                detail=location,
                prompt_number=prompt_number,
            )
        )
    return mismatches


def discover_md_fixture(directory: Path | None = None) -> Path:
    folder = directory or FIXTURE_DIR
    md_named = folder / f"{DEFAULT_FIXTURE_STEM}.md"
    if md_named.is_file():
        return md_named

    md_files = sorted(folder.glob("*.md"))
    if len(md_files) == 1:
        return md_files[0]
    raise FileNotFoundError(
        f"Expected {DEFAULT_FIXTURE_STEM}.md in {folder} "
        f"(or exactly one .md), found {len(md_files)}."
    )


def discover_docx_fixture(directory: Path | None = None) -> Path:
    folder = directory or FIXTURE_DIR
    docx_named = folder / f"{DEFAULT_FIXTURE_STEM}.docx"
    if docx_named.is_file():
        return docx_named

    docx_files = sorted(folder.glob("*.docx"))
    if len(docx_files) == 1:
        return docx_files[0]
    raise FileNotFoundError(
        f"Expected {DEFAULT_FIXTURE_STEM}.docx in {folder} "
        f"(or exactly one .docx), found {len(docx_files)}."
    )


def run_compare(
    md_path: str | Path | None = None,
    docx_path: str | Path | None = None,
    fields: list[ChecklistField] | None = None,
) -> list[FieldMismatch]:
    md_path = Path(md_path or discover_md_fixture())
    if docx_path is None:
        raise ValueError("docx_path is required for compare")
    docx_path = Path(docx_path)
    if fields is None:
        fields = attach_docx_refs(
            load_prompts_csv(CHECKLIST_PROMPTS_CSV),
            _REPO_ROOT / CHECKLIST_TEMPLATE_DOCX,
        )
    md_answers = parse_md_answers(md_path)
    return compare_answers(md_answers, docx_path, fields)


def format_report(
    mismatches: list[FieldMismatch],
    md_path: Path,
    docx_path: Path,
) -> str:
    lines = [
        f"Compared MD (truth):   {md_path}",
        f"         DOCX (test):  {docx_path}",
        f"Mismatches: {len(mismatches)}",
        "",
    ]
    for m in mismatches:
        lines.extend(m.format_lines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Compare checklist MD export to DOCX; print report and return exit code."""
    parser = argparse.ArgumentParser(
        description="Compare checklist MD export (truth) to DOCX export.",
    )
    parser.add_argument(
        "--md",
        type=Path,
        default=None,
        help=f"Markdown path (default: {DEFAULT_FIXTURE_STEM}.md in tests/docx)",
    )
    parser.add_argument(
        "--docx",
        type=Path,
        default=None,
        help=f"Word path (default: {DEFAULT_FIXTURE_STEM}.docx in tests/docx)",
    )
    args = parser.parse_args(argv)

    try:
        md_path = Path(args.md) if args.md else discover_md_fixture()
        docx_path = Path(args.docx) if args.docx else discover_docx_fixture()
        mismatches = run_compare(md_path, docx_path)
        report = format_report(mismatches, md_path, docx_path)
        print(report, end="")
        return 1 if mismatches else 0
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
