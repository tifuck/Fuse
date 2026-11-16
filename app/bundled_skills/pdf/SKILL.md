---
name: pdf
description: Read, extract, fill, merge, split and create PDFs with open-source tools, then verify by re-reading the output. Use when the user gives a PDF to read or edit, asks to fill a PDF form, combine or split PDFs, or generate a PDF.
metadata:
  category: Docs
  inspired-by: anthropics/skills
---
# PDF

Check what is installed before choosing a tool: `python3 -c "import pypdf, pdfplumber"`, `which qpdf pdftotext pdftoppm`. Install only with the user's agreement, preferably into a virtualenv. Never overwrite the input file; write to a new path.

## Inspect
- Metadata and page count: `pdfinfo in.pdf` or `pypdf.PdfReader("in.pdf")` (`len(reader.pages)`, `reader.metadata`).
- Encrypted? `reader.is_encrypted`; ask the user for the password rather than trying to break it.
- Scanned or text? If `pdftotext` returns little or no text, the pages are images and need OCR (`ocrmypdf` or `tesseract`, if available).

## Extract text and tables
- Plain text with layout: `pdftotext -layout in.pdf out.txt` (add `-f N -l M` for page ranges).
- Python: `pdfplumber.open(path)` then `page.extract_text()`; tables with `page.extract_tables()`. Check table output against the page; merged cells and multi-line rows often split wrongly.
- Images of pages for visual checks: `pdftoppm -png -r 100 -f 1 -l 1 in.pdf page`.

## Merge, split, rotate
- qpdf: merge `qpdf --empty --pages a.pdf b.pdf -- out.pdf`; pages 1-3 `qpdf in.pdf --pages . 1-3 -- out.pdf`; rotate `qpdf in.pdf out.pdf --rotate=+90:1`.
- pypdf: `PdfWriter()`, `writer.add_page(page)` or `writer.append(reader)`, then `writer.write(path)`. `page.rotate(90)` for rotation.
- Repair or linearize a damaged file: `qpdf in.pdf out.pdf` often fixes structure; `qpdf --check in.pdf` reports problems.

## Fill forms
1. List fields: `reader.get_fields()` (name, type, current value, options for choices). If there are none, the form is not fillable; overlay text instead (see below).
2. Map the user's data to exact field names; ask when a mapping is ambiguous.
3. Fill: `writer = PdfWriter(clone_from=reader)`, then `writer.update_page_form_field_values(writer.pages[i], {...}, auto_regenerate=False)` and set `writer.set_need_appearances_writer(True)` so viewers render values.
4. Checkboxes take their export value (often `/Yes`), not `True`.
5. Flatten only if the user asks.

For non-fillable forms: draw text with reportlab on a blank page of the same size at measured coordinates (PDF origin is bottom-left, units are points), then merge it onto the original page with `page.merge_page(overlay_page)`. Render the result to PNG and check placement.

## Create
- reportlab for programmatic documents: `SimpleDocTemplate` with `Paragraph`, `Table`, `Spacer`, `PageBreak` for flowing content; `canvas.Canvas` for precise positioning.
- For rich documents, writing HTML or Markdown and converting (pandoc, or a headless browser print) is often simpler; use what is installed.
- Register a TTF font for non-Latin text; built-in fonts cover only basic Latin.

## Verify
Re-open every output: page count matches, `pdftotext` shows the expected text, form fields read back with the filled values, and render one or two pages to PNG to look at layout when it matters. Report the output path and what you checked.
