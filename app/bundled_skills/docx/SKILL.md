---
name: docx
description: Create and edit Word .docx files with python-docx or pandoc, using real styles, headings and tables, then verify by converting back to text. Use when the user asks for a Word document, report or letter, or to edit or review a .docx.
metadata:
  category: Docs
  inspired-by: anthropics/skills
---
# Word documents (.docx)

Check tools first: `python3 -c "import docx"` (package `python-docx`), `which pandoc libreoffice soffice`. Install only with the user's agreement. Never overwrite the user's original; save to a new file unless told otherwise.

## Choose the approach
- New document from content (report, memo, notes): write Markdown and run `pandoc in.md -o out.docx`. Add `--reference-doc=template.docx` to inherit fonts, margins and heading styles from a template.
- Precise layout, tables with formatting, editing an existing file: python-docx.
- Reading a document: `pandoc in.docx -t markdown` keeps headings, lists and tables; python-docx gives paragraphs with their style names.

## Creating with python-docx
- Use built-in styles, not manual formatting: `doc.add_heading(text, level=1)`, `doc.add_paragraph(text, style="List Bullet")`. Real heading styles make the navigation pane and table of contents work.
- Change look through styles: `doc.styles["Normal"].font.name` and `.size`, heading styles likewise. Avoid setting fonts run by run.
- Tables: `doc.add_table(rows, cols, style="Table Grid")`; fill `table.cell(r, c).text`; mark the header row bold or use a table style with a header.
- Page setup via `doc.sections[0]`: `page_width`, `orientation`, margins (use `docx.shared.Cm`/`Inches`).
- Page breaks: `doc.add_page_break()`. Images: `doc.add_picture(path, width=Inches(6))`.
- Headers and footers: `section.header.paragraphs[0].text`. Page number fields need raw XML; keep it simple or use a template that already has them.
- A table of contents is a field that Word updates on open; insert it via a reference template or tell the user to update fields.

## Editing an existing document
- Text is split into runs; a phrase can span several runs, so a naive `run.text.replace` misses it. Search at paragraph level, then rewrite the runs of that paragraph, keeping the first run's formatting.
- Iterate tables and headers/footers too, not only `doc.paragraphs`.
- Preserve styles, numbering and section settings; change only what was asked.

## Tracked changes and comments
python-docx cannot create tracked changes and has limited comment support (recent versions add basic comments). If the user needs redlines, say so and offer options: produce a clean edited copy plus a summary of changes, or compare the two versions in Word or LibreOffice. When reading a file that contains tracked changes, note that python-docx text may include deleted text or omit insertions; `pandoc --track-changes=all` shows both.

## Verify
- Reopen with python-docx and check headings, paragraph count and table contents.
- Convert back: `pandoc out.docx -t plain` and read the result for missing or duplicated text.
- If LibreOffice is available, `soffice --headless --convert-to pdf out.docx` and inspect the PDF for layout problems.
Report the output path and what you verified.
