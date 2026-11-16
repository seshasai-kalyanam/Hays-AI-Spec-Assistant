"""Load checklist prompts from db/prompts.csv and optional DOCX export references."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PROMPTS_CSV = "db/prompts.csv"
EXTRACT_TEXT_MARKER = "extract explicit value from text"
NOTES_MARKERS = ("additional notes", "are there any other", "are there any additional")
DOCX_NOTES_TEMPLATE_LABEL = "Additional Information/Notes:"
DOCX_SPEC_SECTION_TEMPLATE_LABEL = "Spec Section"
DOCX_LETTER_NUMBERED_MAJOR_SECTIONS: tuple[str, ...] = (
    "General Electrical Provisions",
    "Testing/Study Requirements",
    "Insulated Conductors - 600V",
)
DOCX_LABEL_PREFIX_RE = re.compile(r"^(?:\d+|[A-Z]+)\.\s*")
SPECIFIED_RE = re.compile(r"specified '([^']+)'", re.IGNORECASE)
FOLLOWUP_RE = re.compile(
    r"Regarding (.+?) - (.+?), what is the specified '([^']+)'\?",
    re.IGNORECASE,
)


@dataclass
class DocxRef:
    """Location in the checklist template for export."""

    table_index: int
    row_index: int
    option_col_map: dict[str, int] = field(default_factory=dict)
    value_col_index: int | None = None


@dataclass
class ChecklistField:
    row_index: int
    prompt_number: int
    category: str
    prompt: str
    possible_answers_raw: str
    allowed_options: list[str]
    field_kind: str
    display_label: str
    docx_ref: DocxRef | None = None


def _parse_prompt_number(raw: str | None, row_index: int) -> int:
    """Parse prompt_number from CSV; fallback to row_index + 1 for legacy files."""
    text = (raw or "").strip()
    if text:
        try:
            n = int(text)
            if n > 0:
                return n
        except ValueError:
            pass
    return row_index + 1


def format_prompt_label(field: ChecklistField) -> str:
    """Display label with prompt number prefix."""
    return f"{field.prompt_number}. {field.display_label}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_csv_path(path: str | Path | None) -> Path:
    if path is None:
        return _repo_root() / DEFAULT_PROMPTS_CSV
    p = Path(path)
    return p if p.is_absolute() else _repo_root() / p


def _extract_display_label(prompt: str) -> str:
    m = re.search(r"specification for '([^']+)'", prompt, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = FOLLOWUP_RE.search(prompt)
    if m:
        return m.group(3).strip()
    m = SPECIFIED_RE.search(prompt)
    if m:
        return m.group(1).strip()
    m = re.search(r"designated Spec Section for (.+)\?", prompt, re.IGNORECASE)
    if m:
        return f"Spec Section ({m.group(1).strip()})"
    if "other requirements" in prompt.lower() or "additional notes" in prompt.lower():
        return "Notes"
    return prompt[:80] + ("..." if len(prompt) > 80 else "")


def _parse_allowed_options(possible_answers: str) -> tuple[list[str], str]:
    raw = (possible_answers or "").strip()
    if not raw:
        return [], "text"
    lower = raw.lower()
    if EXTRACT_TEXT_MARKER in lower or lower in {"gallons", "h"}:
        return [], "text"
    if "notes" in lower and EXTRACT_TEXT_MARKER not in lower:
        return [], "notes"
    options = [o.strip() for o in raw.split(",") if o.strip()]
    if len(options) >= 2:
        return options, "choice"
    if len(options) == 1 and EXTRACT_TEXT_MARKER not in options[0].lower():
        return options, "text"
    return [], "text"


def _infer_field_kind(prompt: str, possible_answers: str, options: list[str]) -> str:
    _, kind_from_answers = _parse_allowed_options(possible_answers)
    pl = prompt.lower()
    if any(m in pl for m in NOTES_MARKERS):
        return "notes"
    if kind_from_answers == "choice" and options:
        return "choice"
    return kind_from_answers


def load_prompts_csv(
    path: str | Path | None = None,
    limit: int | None = None,
) -> list[ChecklistField]:
    """Read prompts CSV (UTF-8) into ChecklistField rows."""
    csv_path = _resolve_csv_path(path)
    fields: list[ChecklistField] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row_index, row in enumerate(reader):
            if limit is not None and row_index >= limit:
                break
            category = (row.get("category") or "").strip()
            prompt = (row.get("prompt") or "").strip()
            possible_raw = (row.get("possible answers") or "").strip()
            options, _ = _parse_allowed_options(possible_raw)
            field_kind = _infer_field_kind(prompt, possible_raw, options)
            fields.append(
                ChecklistField(
                    row_index=row_index,
                    prompt_number=_parse_prompt_number(row.get("prompt_number"), row_index),
                    category=category,
                    prompt=prompt,
                    possible_answers_raw=possible_raw,
                    allowed_options=options,
                    field_kind=field_kind,
                    display_label=_extract_display_label(prompt),
                )
            )
    return fields


def count_prompts_csv(path: str | Path | None = None) -> int:
    """Total data rows in the CSV."""
    csv_path = _resolve_csv_path(path)
    with csv_path.open(encoding="utf-8", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))


def _dedupe_row_options(cells: list[str]) -> list[tuple[int, str]]:
    """Map option label -> first column index (skip label column 0)."""
    seen: dict[str, int] = {}
    for ci, text in enumerate(cells):
        if ci == 0:
            continue
        label = text.strip()
        if label and label not in seen:
            seen[label] = ci
    return [(col, lbl) for lbl, col in seen.items()]


def _normalize_notes_template_label(label: str) -> str:
    """Canonical label for Additional Information/Notes template rows."""
    text = (label or "").strip()
    text = DOCX_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    if text.rstrip(":").strip().lower() == "additional information/notes":
        return DOCX_NOTES_TEMPLATE_LABEL
    return text


def _is_notes_template_label(label: str) -> bool:
    return _normalize_notes_template_label(label) == DOCX_NOTES_TEMPLATE_LABEL


def _normalize_spec_section_template_label(label: str) -> str:
    """Canonical label for Spec Section template rows (with or without leading a.)."""
    text = (label or "").strip().rstrip(":")
    text = DOCX_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    if text.lower() == "spec section":
        return DOCX_SPEC_SECTION_TEMPLATE_LABEL
    return (label or "").strip()


def _is_spec_section_template_label(label: str) -> bool:
    return _normalize_spec_section_template_label(label) == DOCX_SPEC_SECTION_TEMPLATE_LABEL


def _normalize_project_header_label(label: str) -> str:
    text = (label or "").strip().rstrip(":")
    text = DOCX_LABEL_PREFIX_RE.sub("", text, count=1).strip()
    if text.lower() in {"project number", "project name"}:
        return text.title()
    return (label or "").strip()


def _is_project_header_label(label: str) -> bool:
    return _normalize_project_header_label(label) in {"Project Number", "Project Name"}


def _expand_option_col_map(cells: list[str]) -> dict[str, int]:
    """Build option label -> column map, splitting merged multi-line header cells."""
    col_map: dict[str, int] = {}
    for col, lbl in _dedupe_row_options(cells):
        col_map[lbl] = col
        for part in re.split(r"[\r\n]+", lbl):
            piece = part.strip()
            if piece and piece not in col_map:
                col_map[piece] = col
    return col_map


def is_embedded_subfield_ref(docx_ref: DocxRef | None) -> bool:
    """True when export targets an embedded sub-column (e.g. Duration:), not the row label."""
    col = docx_ref.value_col_index if docx_ref else None
    return bool(
        docx_ref
        and col is not None
        and col > 1
        and not docx_ref.option_col_map
    )


def _embedded_subfield_cols(option_col_map: dict[str, int]) -> dict[str, int]:
    """Map sub-field label -> column for embedded value headers (e.g. Duration:, Division:)."""
    out: dict[str, int] = {}
    for header, col in option_col_map.items():
        if not header.rstrip().endswith(":"):
            continue
        sub_label = header.strip().rstrip(":").strip()
        if not sub_label or sub_label.lower() in {"yes", "no"}:
            continue
        out.setdefault(sub_label, col)
    return out


def _is_skipped_template_row(label: str, cells: list[str], option_col_map: dict[str, int]) -> bool:
    """Skip section banners and label-only rows that are not CSV fields."""
    if _is_notes_template_label(label):
        return False
    if _is_spec_section_template_label(label):
        return False
    if _is_project_header_label(label):
        return False
    upper = label.upper()
    if upper.startswith("SECTION ") and ":" in label:
        return True
    if label in {
        "General Electrical Provisions",
        "Testing/Study Requirements",
        "SECTION ONE: SPECIFICATION REVIEW (INSTALLATION)",
        "SECTION TWO: SPECIFICATION REVIEW (SYSTEMS)",
        "SECTION FOUR: MISCELLANEOUS",
    }:
        return True
    if not option_col_map:
        has_value = any(cells[i].strip() for i in range(1, len(cells)))
        if not has_value:
            return True
    return False


def _docx_ref_from_row(table_index: int, row_index: int, cells: list[str]) -> DocxRef | None:
    label = cells[0]
    if _is_notes_template_label(label):
        return DocxRef(
            table_index=table_index,
            row_index=row_index,
            option_col_map={},
            value_col_index=0,
        )
    if _is_spec_section_template_label(label):
        return DocxRef(
            table_index=table_index,
            row_index=row_index,
            option_col_map={},
            value_col_index=1,
        )
    if _is_project_header_label(label):
        return DocxRef(
            table_index=table_index,
            row_index=row_index,
            option_col_map={},
            value_col_index=1,
        )
    option_col_map = _expand_option_col_map(cells)
    if _is_skipped_template_row(label, cells, option_col_map):
        return None
    value_col = 1 if len(cells) > 1 and not option_col_map else None
    return DocxRef(
        table_index=table_index,
        row_index=row_index,
        option_col_map=option_col_map,
        value_col_index=value_col,
    )


def _normalize_mapping_label(text: str) -> str:
    """Normalize template/CSV labels for fuzzy DOCX mapping."""
    cleaned = (text or "").strip().rstrip(":")
    cleaned = DOCX_LABEL_PREFIX_RE.sub("", cleaned, count=1).strip()
    cleaned = cleaned.replace("\ufffd", "")
    cleaned = re.sub(r"req['\u2019\u00b4]?d", "required", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace('"', "").replace("\u201d", "").replace("\u201c", "")
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _labels_match_mapping(csv_label: str, template_label: str) -> bool:
    """True when CSV and template row labels refer to the same checklist field."""
    a = _normalize_mapping_label(csv_label)
    b = _normalize_mapping_label(template_label)
    if not a or not b:
        return False
    if a == b:
        return True
    if a in b or b in a:
        return True
    if a == "other" and b == "other":
        return True
    if "additional information notes" in a and "additional information notes" in b:
        return True
    return False


def _normalize_section_key(section: str) -> str:
    return _normalize_mapping_label(section)


def _take_next_docx_ref(
    section_label_map: dict[tuple[str, str], list[DocxRef]],
    label_use_index: dict[tuple[str, str], int],
    map_key: tuple[str, str],
) -> DocxRef | None:
    refs_for_label = section_label_map.get(map_key, [])
    use_i = label_use_index.get(map_key, 0)
    ref = refs_for_label[use_i] if use_i < len(refs_for_label) else None
    label_use_index[map_key] = use_i + 1
    return ref


def _resolve_docx_ref(
    section: str,
    tlabel: str,
    section_label_map: dict[tuple[str, str], list[DocxRef]],
    label_use_index: dict[tuple[str, str], int],
) -> DocxRef | None:
    """Resolve a template ref: exact match first, then normalized/fuzzy within section."""
    ref = _take_next_docx_ref(section_label_map, label_use_index, (section, tlabel))
    if ref is not None:
        return ref

    norm_section = _normalize_section_key(section)
    norm_label = _normalize_mapping_label(tlabel)
    exact_norm_keys: list[tuple[str, str]] = []
    fuzzy_keys: list[tuple[str, str]] = []

    for map_key in section_label_map:
        sec, lbl = map_key
        if _normalize_section_key(sec) != norm_section:
            continue
        lbl_norm = _normalize_mapping_label(lbl)
        if lbl_norm == norm_label:
            exact_norm_keys.append(map_key)
        elif _labels_match_mapping(tlabel, lbl):
            fuzzy_keys.append(map_key)

    for map_key in exact_norm_keys + fuzzy_keys:
        ref = _take_next_docx_ref(section_label_map, label_use_index, map_key)
        if ref is not None:
            return ref
    return None


def _enrich_option_col_map(
    table,
    ref: DocxRef,
    allowed_options: list[str],
) -> DocxRef:
    """Add missing allowed_options to col map when headers are empty but columns exist."""
    if not allowed_options or ref.row_index >= len(table.rows):
        return ref
    row = table.rows[ref.row_index]
    col_map = dict(ref.option_col_map)
    for opt in allowed_options:
        if opt in col_map:
            continue
        opt_lower = opt.lower()
        for ci, cell in enumerate(row.cells):
            if ci == 0:
                continue
            if opt_lower in cell.text.lower():
                col_map[opt] = ci
                break
    if any(opt not in col_map for opt in allowed_options):
        option_cols: list[int] = []
        previous_label = ""
        for ci, cell in enumerate(row.cells):
            if ci == 0:
                continue
            label = cell.text.strip()
            if not label:
                continue
            if any(ch in label for ch in ("\ufffd", "\u00bc", "\u00bd", "\u00be")):
                option_cols.append(ci)
            elif label != previous_label:
                option_cols.append(ci)
            previous_label = label
        if len(option_cols) >= len(allowed_options) and any(
            any(ch in row.cells[ci].text for ch in ("\ufffd", "\u00bc", "\u00bd", "\u00be"))
            for ci in option_cols
        ):
            for opt, ci in zip(allowed_options, option_cols, strict=False):
                col_map.setdefault(opt, ci)
    return DocxRef(
        table_index=ref.table_index,
        row_index=ref.row_index,
        option_col_map=col_map,
        value_col_index=ref.value_col_index,
    )


def _template_label_for_field(field: ChecklistField) -> str:
    """Word row label used in the template (may differ from CSV display_label)."""
    if field.field_kind == "notes":
        return DOCX_NOTES_TEMPLATE_LABEL
    m = re.match(r"^Spec Section \((.+)\)$", field.display_label)
    if m:
        return "Spec Section"
    followup = FOLLOWUP_RE.search(field.prompt)
    if followup:
        return followup.group(3).strip()
    return field.display_label


def _section_key_for_field(field: ChecklistField) -> str:
    """Template section name for mapping (category or Spec Section parenthetical)."""
    m = re.match(r"^Spec Section \((.+)\)$", field.display_label)
    if m:
        return m.group(1).strip()
    if field.category == "Conduit":
        if "Regarding Underground" in field.prompt:
            return "Raceways"
        return "Elbow Radius"
    if " - " in field.category:
        return field.category.split(" - ", 1)[0].strip()
    return field.category


def _categories_for_docx_banners(fields: list[ChecklistField]) -> set[str]:
    """CSV categories plus template section banners still used for Conduit rows."""
    categories = {f.category for f in fields}
    if "Conduit" in categories:
        categories.update({"Raceways", "Elbow Radius"})
    return categories


def _index_to_letter_label(index: int) -> str:
    """Map 0-based index to Excel-style letters (A, B, ..., Z, AA)."""
    n = index + 1
    letters = ""
    while n:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _resolve_docx_template_path(template_path: str | Path | None) -> Path:
    root = _repo_root()
    docx_path = Path(template_path) if template_path else root / "notes" / "Hays Spec. Review Checklist.docx"
    return docx_path if docx_path.is_absolute() else root / docx_path


def build_docx_row_major_section_map(
    template_path: str | Path | None = None,
) -> dict[tuple[int, int], str]:
    """Map (table_index, row_index) to SECTION ONE major banner for letter numbering."""
    from docx import Document

    docx_path = _resolve_docx_template_path(template_path)
    if not docx_path.is_file():
        return {}

    doc = Document(str(docx_path))
    row_map: dict[tuple[int, int], str] = {}
    current = ""
    letter_sections = set(DOCX_LETTER_NUMBERED_MAJOR_SECTIONS)
    for table_index, table in enumerate(doc.tables):
        for row_index, row in enumerate(table.rows):
            cells = [c.text.strip() for c in row.cells]
            if not cells or not cells[0]:
                continue
            label = DOCX_LABEL_PREFIX_RE.sub("", cells[0], count=1).strip()
            if label in letter_sections:
                current = label
                continue
            if label.startswith("Insulated Conductors -") and label not in letter_sections:
                current = ""
                continue
            if label.upper().startswith("SECTION ") and ":" in label:
                current = ""
                continue
            ref = _docx_ref_from_row(table_index, row_index, cells)
            if ref is not None and current:
                row_map[(table_index, row_index)] = current
    return row_map


def build_docx_letter_export_prefixes(
    fields: list[ChecklistField],
    template_path: str | Path | None = None,
) -> dict[int, str]:
    """Map field row_index -> Word export prefix (A., B., ...) within major SECTION ONE bands."""
    row_major = build_docx_row_major_section_map(template_path)
    by_section: dict[str, list[ChecklistField]] = {
        name: [] for name in DOCX_LETTER_NUMBERED_MAJOR_SECTIONS
    }
    for field in fields:
        if field.docx_ref is None:
            continue
        major = row_major.get((field.docx_ref.table_index, field.docx_ref.row_index))
        if major:
            by_section[major].append(field)
    prefixes: dict[int, str] = {}
    for major in DOCX_LETTER_NUMBERED_MAJOR_SECTIONS:
        ordered = sorted(by_section[major], key=lambda item: item.prompt_number)
        for index, field in enumerate(ordered):
            prefixes[field.row_index] = f"{_index_to_letter_label(index)}. "
    return prefixes


def _is_category_section_banner(
    label: str,
    cells: list[str],
    option_col_map: dict[str, int],
    categories: set[str],
) -> bool:
    """True when a template row opens a CSV category section."""
    if label not in categories:
        return False
    if _is_skipped_template_row(label, cells, option_col_map):
        return True
    return set(option_col_map.keys()) == {label}


def build_docx_section_label_map(
    template_path: str | Path | None = None,
    categories: set[str] | None = None,
) -> dict[tuple[str, str], list[DocxRef]]:
    """
    Map (section, row_label) to DOCX refs in document order.
    Section follows category banner rows; duplicate row labels are FIFO per section.
    """
    from docx import Document

    root = _repo_root()
    docx_path = Path(template_path) if template_path else root / "notes" / "Hays Spec. Review Checklist.docx"
    if not docx_path.is_file():
        return {}

    doc = Document(str(docx_path))
    label_map: dict[tuple[str, str], list[DocxRef]] = {}
    current_section = ""
    cat_set = categories or set()
    for table_index, table in enumerate(doc.tables):
        for row_index, row in enumerate(table.rows):
            cells = [c.text.strip() for c in row.cells]
            if not cells or not cells[0]:
                continue
            raw_label = cells[0]
            raw_label_unprefixed = DOCX_LABEL_PREFIX_RE.sub("", raw_label, count=1).strip()
            label = _normalize_notes_template_label(raw_label)
            if _is_spec_section_template_label(raw_label):
                label = DOCX_SPEC_SECTION_TEMPLATE_LABEL
            elif _is_project_header_label(raw_label):
                label = _normalize_project_header_label(raw_label)
            option_col_map = _expand_option_col_map(cells)
            if cat_set and _is_category_section_banner(label, cells, option_col_map, cat_set):
                current_section = label
            elif cat_set and _is_category_section_banner(
                raw_label_unprefixed, cells, option_col_map, cat_set
            ):
                current_section = raw_label_unprefixed
            ref = _docx_ref_from_row(table_index, row_index, cells)
            if ref is None:
                continue
            label_map.setdefault((current_section, label), []).append(ref)
            for sub_label, col in _embedded_subfield_cols(ref.option_col_map).items():
                label_map.setdefault((current_section, sub_label), []).append(
                    DocxRef(
                        table_index=table_index,
                        row_index=row_index,
                        option_col_map={},
                        value_col_index=col,
                    )
                )
            if _is_project_header_label(raw_label):
                label_map.setdefault(("Project Details", label), []).append(ref)
    return label_map


def attach_docx_refs(fields: list[ChecklistField], template_path: str | Path | None = None) -> list[ChecklistField]:
    """Attach docx_ref by matching CSV category/label to template section rows."""
    from docx import Document

    root = _repo_root()
    docx_path = Path(template_path) if template_path else root / "notes" / "Hays Spec. Review Checklist.docx"
    categories = _categories_for_docx_banners(fields)
    section_label_map = build_docx_section_label_map(template_path, categories)
    doc = Document(str(docx_path)) if docx_path.is_file() else None
    label_use_index: dict[tuple[str, str], int] = {}
    out: list[ChecklistField] = []

    for f in fields:
        section = _section_key_for_field(f)
        tlabel = _template_label_for_field(f)
        ref = _resolve_docx_ref(section, tlabel, section_label_map, label_use_index)

        if ref is not None and doc is not None and f.allowed_options and f.field_kind == "choice":
            table = doc.tables[ref.table_index]
            ref = _enrich_option_col_map(table, ref, f.allowed_options)

        out.append(
            ChecklistField(
                row_index=f.row_index,
                prompt_number=f.prompt_number,
                category=f.category,
                prompt=f.prompt,
                possible_answers_raw=f.possible_answers_raw,
                allowed_options=f.allowed_options,
                field_kind=f.field_kind,
                display_label=f.display_label,
                docx_ref=ref,
            )
        )
    return out
