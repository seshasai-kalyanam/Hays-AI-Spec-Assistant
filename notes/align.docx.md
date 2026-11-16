Align the checklist DOCX export so every prompt answer from `db/prompts.csv` has a reliable destination in `notes/Hays Spec. Review Checklist.docx`, and generated DOCX files match the Markdown truth fixture in `tests/docx`.

Context:
- The app exports checklist answers through `utils/results_form.py::export_checklist_docx`.
- Prompt-to-template mapping is built in `utils/checklist_schema.py` with `attach_docx_refs`, `build_docx_section_label_map`, and `DocxRef`.
- Regression coverage lives in `tests/docx/test_md_docx_fixture.py` and comparison helpers in `tests/docx/md_docx_compare.py`.
- The template is `notes/Hays Spec. Review Checklist.docx`.
- The Markdown/DOCX truth fixtures are `tests/docx/spec_compliance_checklist.md` and `tests/docx/spec_compliance_checklist.docx`.

What to do:
1. Inspect all prompts from `db/prompts.csv` after `attach_docx_refs(...)` and confirm every prompt has a `docx_ref`.
2. For every choice prompt, confirm every allowed option resolves to a concrete Word checkbox/value column through `_resolve_option_column`.
3. Fix mismatches at the best source:
   - If the Word template label is wrong, update `notes/Hays Spec. Review Checklist.docx`.
   - If the CSV option is wrong, update `db/prompts.csv`.
   - If the template uses equivalent text that Python reads differently, normalize matching in code without weakening unrelated mappings.
4. Keep DOCX export behavior aligned with the current template shape:
   - choice fields should mark the intended checkbox only;
   - text fields should write into the mapped value cell;
   - notes fields should write into the Additional Information/Notes row;
   - embedded subfields should remain attached to their parent row and visible in the exported prompt numbering.
5. Do not remove existing functionality or unrelated template content. Keep code changes small and function-scoped.

Validation:
- Run `python -m pytest tests/docx/test_md_docx_fixture.py`.
- Run a coverage check equivalent to:
  `attach_docx_refs(load_prompts_csv(CHECKLIST_PROMPTS_CSV), CHECKLIST_TEMPLATE_DOCX)` and assert:
  - zero fields have `docx_ref is None`;
  - zero allowed choice options return `None` from `_resolve_option_column`.
- If any fixture comparison fails, use `format_report(...)` output to identify whether the issue is mapping, checkbox marking, text writing, or fixture drift.

Expected result:
- No Export panel warning for answered fields caused by missing Word destinations.
- Generated DOCX exports preserve all answered prompt values in the correct template locations.
- DOCX fixture tests pass.
