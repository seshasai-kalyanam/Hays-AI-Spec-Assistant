"""Pure helpers for checklist field status and export values (no Streamlit)."""

from __future__ import annotations

from dataclasses import dataclass, field

from utils.checklist_schema import ChecklistField
from utils.results_form import (
    CANNOT_ANSWER_FROM_KB,
    _normalize_option_label,
    is_checklist_answered_value,
)


def resolve_field_status(
    *,
    is_answered: bool,
    has_review_warning: bool,
    stored_status: str,
    latest_log_state: str | None,
) -> str:
    """Resolve user-facing field status from form and autofill state."""
    if has_review_warning:
        return "needs_review"
    if is_answered:
        return "answered"
    if stored_status:
        return stored_status
    if latest_log_state in {CANNOT_ANSWER_FROM_KB, "error"}:
        return latest_log_state
    return "unanswered"


def export_values(values: dict, review_row_indices: set[int] | frozenset[int]) -> dict:
    """Return a copy of values with needs-review rows blanked for export."""
    if not review_row_indices:
        return dict(values)
    out = dict(values)
    for row_index in review_row_indices:
        if row_index in out:
            out[row_index] = ""
    return out


def review_row_indices_from_flags(flags: dict[int, bool]) -> frozenset[int]:
    """Build the set of row indices flagged for review from a row_index -> bool map."""
    return frozenset(row_index for row_index, flagged in flags.items() if flagged)


REASON_NEEDS_REVIEW = "needs_review"
REASON_UNMAPPED = "unmapped"
REASON_MISSING_OPTION = "missing_option_column"


@dataclass
class ExportAlignmentIssue:
    """One answered UI value that will not appear correctly in Word export."""

    prompt_number: int
    display_label: str
    category: str
    reason: str
    value: str


@dataclass
class ExportAlignmentReport:
    """Summary of UI answers that differ from what Word export will write."""

    issues: list[ExportAlignmentIssue] = field(default_factory=list)

    @property
    def needs_review_count(self) -> int:
        return sum(1 for issue in self.issues if issue.reason == REASON_NEEDS_REVIEW)

    @property
    def unmapped_count(self) -> int:
        return sum(1 for issue in self.issues if issue.reason == REASON_UNMAPPED)

    @property
    def missing_option_count(self) -> int:
        return sum(
            1 for issue in self.issues if issue.reason == REASON_MISSING_OPTION
        )

    @property
    def total_count(self) -> int:
        return len(self.issues)


def _choice_option_in_col_map(selected: str, allowed_options: list[str], col_map: dict) -> bool:
    """True when the selected choice can be resolved to a template column."""
    if not col_map:
        return False
    if selected in col_map:
        return True
    selected_lower = selected.lower()
    selected_norm = _normalize_option_label(selected)
    for key in col_map:
        if key.lower() == selected_lower:
            return True
        if _normalize_option_label(str(key)) == selected_norm:
            return True
    for opt in allowed_options:
        if opt.lower() == selected_lower and opt in col_map:
            return True
    return False


def diagnose_export_alignment(
    fields: list[ChecklistField],
    values: dict,
    review_row_indices: set[int] | frozenset[int],
) -> ExportAlignmentReport:
    """Find answered UI values that Word export will omit or cannot place in the template."""
    issues: list[ExportAlignmentIssue] = []

    for checklist_field in fields:
        raw_value = values.get(checklist_field.row_index)
        if not is_checklist_answered_value(raw_value):
            continue

        display_value = str(raw_value).strip()
        base = ExportAlignmentIssue(
            prompt_number=checklist_field.prompt_number,
            display_label=checklist_field.display_label,
            category=checklist_field.category,
            reason="",
            value=display_value,
        )

        if checklist_field.row_index in review_row_indices:
            issues.append(
                ExportAlignmentIssue(
                    prompt_number=base.prompt_number,
                    display_label=base.display_label,
                    category=base.category,
                    reason=REASON_NEEDS_REVIEW,
                    value=base.value,
                )
            )
            continue

        if checklist_field.docx_ref is None:
            issues.append(
                ExportAlignmentIssue(
                    prompt_number=base.prompt_number,
                    display_label=base.display_label,
                    category=base.category,
                    reason=REASON_UNMAPPED,
                    value=base.value,
                )
            )
            continue

        if checklist_field.field_kind == "choice" and checklist_field.allowed_options:
            col_map = checklist_field.docx_ref.option_col_map or {}
            if not _choice_option_in_col_map(
                display_value,
                checklist_field.allowed_options,
                col_map,
            ):
                issues.append(
                    ExportAlignmentIssue(
                        prompt_number=base.prompt_number,
                        display_label=base.display_label,
                        category=base.category,
                        reason=REASON_MISSING_OPTION,
                        value=base.value,
                    )
                )

    return ExportAlignmentReport(issues=issues)


def format_export_alignment_summary(report: ExportAlignmentReport) -> str:
    """One-line summary for the Export panel."""
    if not report.issues:
        return ""

    parts: list[str] = []
    if report.needs_review_count:
        parts.append(f"**{report.needs_review_count}** needs review")
    if report.unmapped_count:
        parts.append(f"**{report.unmapped_count}** not mapped to the Word template")
    if report.missing_option_count:
        parts.append(
            f"**{report.missing_option_count}** missing a template checkbox column"
        )
    return (
        f"**{report.total_count}** answered field(s) will not appear in Word as shown: "
        + ", ".join(parts)
        + "."
    )


def format_export_alignment_issue_line(issue: ExportAlignmentIssue) -> str:
    """Single issue line for an export alignment expander."""
    reason_labels = {
        REASON_NEEDS_REVIEW: "Needs review (withheld from export)",
        REASON_UNMAPPED: "No Word template row mapped",
        REASON_MISSING_OPTION: "Checkbox option not found in template columns",
    }
    reason_text = reason_labels.get(issue.reason, issue.reason)
    return (
        f"Prompt **{issue.prompt_number}** · **{issue.display_label}** "
        f"({issue.category}) — {reason_text}. Result: `{issue.value}`"
    )
