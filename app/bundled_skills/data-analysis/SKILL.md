---
name: data-analysis
description: Analyse a CSV, JSON, Excel or SQL dataset by checking schema and quality first, stating assumptions, computing with pandas or DuckDB and sanity-checking results. Use when the user asks a question about data or wants stats or trends.
metadata:
  category: Docs
---
# Data analysis

Answer the user's question with numbers you can defend. Work on copies; never modify the source data or run write queries against a database without asking.

## 1. Clarify the question
Restate it as something computable: the metric, the population, the time range, the grouping. If a term is ambiguous ("active user", "revenue"), pick a definition, state it, and continue.

## 2. Inspect before computing
- Size and shape: file size, row and column counts. For large files, sample first or use DuckDB, which queries CSV/Parquet/JSON directly without loading everything: `duckdb -c "DESCRIBE SELECT * FROM 'data.csv'"`.
- Schema: column names, inferred types, a few sample rows (`df.head()`, `df.dtypes`).
- Quality: nulls per column, duplicate rows and duplicate keys, out-of-range values, unparsed dates, numbers stored as text, inconsistent categories ("US", "usa", "United States"), units and currencies, time zones.
- For SQL sources: read the schema and row counts, use `LIMIT` while exploring, and only run `SELECT`.
Write down every quality issue and how you handle it (drop, fill, fix, or keep and flag).

## 3. Compute
- pandas for in-memory work; DuckDB for large files or when SQL is clearer. Keep the analysis in a script or notebook so it can be re-run.
- Make filters explicit and count rows before and after each one.
- Joins: check key uniqueness first; compare row counts after the join to catch fan-out.
- Aggregations: say whether you use mean or median and why; report counts alongside rates.
- Time series: fix the time zone, use complete periods, beware partial current month.
- Comparisons: give absolute and relative change; with small samples, say the result is noisy rather than claiming significance.

## 4. Sanity-check
- Totals reconcile with the raw data (sum of groups equals overall).
- Numbers are plausible in magnitude; percentages sum where they should.
- Recompute one key figure a second way (different tool or a manual subset).
- Look for Simpson's paradox when groups differ a lot in size.
- Outliers: inspect the top and bottom rows before trusting a mean.

## 5. Present
- Lead with the direct answer in one or two sentences, with the key numbers.
- Then a short table or list of supporting figures, with units and the time range.
- Charts only when they add clarity (matplotlib or what the project uses); label axes and units, start bar charts at zero.
- List assumptions, data issues and exclusions, and what would change the conclusion.
- Separate correlation from causation explicitly.
- Provide the script or query used so the user can reproduce it.
