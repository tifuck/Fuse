---
name: xlsx
description: Create and edit Excel spreadsheets with openpyxl or pandas, keeping live formulas, formats and sheets, then verify by reloading. Use when the user asks for an .xlsx, a financial model or tracker, or to read, clean or update a spreadsheet.
metadata:
  category: Docs
  inspired-by: anthropics/skills
---
# Spreadsheets (.xlsx)

Check tools: `python3 -c "import openpyxl, pandas"`, `which soffice libreoffice`. Install only with the user's agreement. Work on a copy; never overwrite the user's file unless asked.

## Pick the tool
- pandas for reading and analysing data: `pd.read_excel(path, sheet_name=None)` returns all sheets. Writing with pandas drops formulas, formats and charts of an existing file.
- openpyxl for building or editing workbooks where formulas, styles, widths, merged cells and multiple sheets matter.
- Editing an existing workbook: `load_workbook(path)` keeps formulas. `load_workbook(path, data_only=True)` returns the last cached values instead; never save a workbook opened with `data_only=True`, it replaces formulas with values.

## Formulas, not hardcoded numbers
- Any value derived from other cells is a formula: `ws["D2"] = "=B2*C2"`, totals `=SUM(D2:D100)`. Do not compute in Python and paste the result.
- Put assumptions (rates, growth, prices) in labelled input cells and reference them, ideally absolute (`$B$1`) or via defined names.
- Use English function names and comma separators in openpyxl, whatever the user's locale.
- Avoid volatile or new functions (`INDIRECT`, `OFFSET`, dynamic arrays) unless needed; they can break in older Excel or LibreOffice.
- Cross-sheet references: `='Sheet Name'!A1`.

## Structure and formatting
- One table per sheet where possible, header row in row 1, no blank rows inside data. Separate sheets for inputs, calculations and outputs in models.
- Number formats instead of rounding: `cell.number_format = "#,##0.00"`, `"0.0%"`, `"yyyy-mm-dd"`. Store dates as dates, not text.
- Freeze headers `ws.freeze_panes = "A2"`, set column widths, bold headers, enable `ws.auto_filter.ref = ws.dimensions`.
- Optional conventions for models: inputs in blue font, formulas in black, links to other sheets in green. Follow the user's existing convention if there is one.
- Charts via `openpyxl.chart` (BarChart, LineChart) referencing cell ranges, so they update with the data.

## Recalculation caveat
openpyxl writes formulas but does not calculate them. A new file has no cached values until Excel or LibreOffice opens it, so pandas or `data_only=True` will read `None`. To compute and check values, recalculate headlessly if LibreOffice is available: `soffice --headless --convert-to xlsx --outdir out/ file.xlsx`, then load the converted copy with `data_only=True`. If values still read `None`, say the file must be opened and saved in Excel or LibreOffice before cached values exist.

## Verify
1. Reload with openpyxl: sheet names, dimensions, a sample of formulas in key cells.
2. After recalculation, scan every cell for errors: `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`.
3. Spot-check two or three totals by hand in Python against the source data.
4. Confirm no formula was replaced by a constant in edited regions.
Report the output path, sheets created and checks run; say if values were not recalculated.
