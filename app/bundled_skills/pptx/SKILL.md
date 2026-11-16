---
name: pptx
description: Create and edit PowerPoint decks with python-pptx using real layouts, one idea per slide, a consistent theme and charts, then verify by listing slide text. Use when the user asks for slides, a presentation, a pitch deck or edits to a .pptx.
metadata:
  category: Docs
  inspired-by: anthropics/skills
---
# PowerPoint decks (.pptx)

Check tools: `python3 -c "import pptx"` (package `python-pptx`), `which soffice pdftoppm`. Install only with the user's agreement. Save to a new file; do not overwrite the original unless asked.

## Plan the deck first
- Write an outline: title, audience, the one message, then one line per slide.
- One idea per slide. The slide title states the takeaway ("Churn fell 18% after onboarding change"), not a topic ("Churn").
- Keep text short: about 3-5 bullets, a few words each. Detail goes in speaker notes (`slide.notes_slide.notes_text_frame.text`).
- Prefer a chart, table, image or big number over a bullet list when the content allows.

## Build with python-pptx
- Start from the user's template if given: `Presentation("template.pptx")`. The built-in default is 4:3; for 16:9 set `prs.slide_width = Inches(13.333)` and `prs.slide_height = Inches(7.5)`, but its layouts keep 4:3 placeholder geometry, so position shapes yourself or use a 16:9 template.
- Use slide layouts and placeholders (`prs.slide_layouts[i]`, `slide.placeholders`) so text inherits the theme. Print layout names and placeholder indices first; they differ per template.
- Consistent theme: one font family, two or three colors with roles, the same margins on every slide. Define them once as constants.
- Text boxes: `slide.shapes.add_textbox(x, y, w, h)`; set `text_frame.word_wrap = True` and size fonts explicitly (titles about 32-40 pt, body at least 18 pt).
- Images: `slide.shapes.add_picture(path, left, top, width=...)`; set only width or height to keep the aspect ratio.
- Charts: `CategoryChartData` plus `slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, ...)`. Native charts stay editable, unlike pasted images.
- Tables: `slide.shapes.add_table(rows, cols, ...)`; keep them under about 6x6 on a slide.

## Editing an existing deck
- Inspect first: for each slide, list layout name, shapes, their types and text.
- Replace text at run level to keep formatting; a word can span several runs.
- python-pptx cannot reorder or delete slides through a public API; the known workaround edits the slide id list. Tell the user before relying on it, and verify the result opens.
- Keep existing themes, masters and animations untouched unless asked.

## Layout checks
- Nothing overflows its box or the slide edge; python-pptx does not shrink text automatically.
- Alignment is consistent across slides (same title position, same left margin).
- Contrast is readable: dark text on light backgrounds or the reverse.

## Verify
1. Reopen the file and print each slide number, title and all text; check order, typos and that no placeholder text like "Click to add title" remains.
2. If LibreOffice is available, render: `soffice --headless --convert-to pdf deck.pptx`, then `pdftoppm -png -r 60 deck.pdf slide`, and look at the images for overflow and overlap.
Report the output path, slide count and what you checked.
