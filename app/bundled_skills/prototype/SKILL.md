---
name: prototype
description: Build a throwaway, time-boxed prototype that answers one specific question fast, then report what was learned. Use when a design hinges on an unknown (feasibility, performance, an API, UX feel) before committing to it.
metadata:
  category: Plan
  inspired-by: mattpocock/skills
---
# Prototype

A prototype exists to answer a question, not to become the product. Optimise for speed of learning.

## 1. Frame the question

Before writing code, state in one message:

- **Question**: one sentence with a yes/no or measurable answer. "Can we stream 10k rows to the browser under 1 s with SSE?" not "try SSE".
- **Success signal**: what result means yes, what means no.
- **Time box**: a concrete budget (for example 30-60 minutes of work or a fixed number of attempts).
- **Out of scope**: auth, error handling, styling, tests, configuration, unless the question is about them.

If the user has not given a question, propose one and confirm it.

## 2. Isolate it

- Put it outside production code: a scratch directory such as `prototypes/<topic>/` or the system temp dir, or a throwaway branch. Ask before adding it to the main tree.
- Name it so nobody mistakes it for real code, and add a header comment or README line: `PROTOTYPE - disposable, do not import or ship`.
- Prefer existing dependencies. If a new package is required to answer the question, install it only inside the prototype (separate venv or package dir) and say so.
- Never point a prototype at production data, real credentials or shared services without explicit permission. Use fakes, fixtures or local copies.

## 3. Build the smallest thing

- Hardcode everything not under test. Copy-paste freely.
- Make the result observable: print timings, sizes, outputs, or render the one screen that matters.
- Run it. A prototype that has not run has answered nothing.
- If the time box runs out, stop and report what you know; do not quietly extend it.

## 4. Report

```
Question: ...
Answer: yes / no / partly -- one line
Evidence: commands run and key numbers or observations
Surprises: things that would change the design
Recommendation: proceed / change approach / investigate X next
Location: <path>; delete when done
```

Separate measured facts from guesses. Mention conditions that could make the result unrepresentative (tiny data set, local machine, warm cache).

## 5. Clean up

Ask whether to keep the prototype for reference or delete it. Never fold prototype code into production as-is; if the approach wins, rebuild it properly with tests from a plan.
