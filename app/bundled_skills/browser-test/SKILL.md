---
name: browser-test
description: Test a web app in a real browser with Playwright, asserting on what the user sees. Use when verifying a UI change, reproducing a frontend bug, checking a user flow end to end, or capturing screenshots and console errors.
metadata:
  category: Quality
  inspired-by: anthropics/skills
---
# Browser test

Drive the real app in a headless browser and assert on visible state, not on implementation details.

## Setup
- Use Playwright from the project if present (`@playwright/test` in package.json, `playwright` in Python deps). Reuse its config and existing tests.
- If it is missing, ask before installing it and the browser binaries (`pip install playwright && playwright install chromium`, or `npm i -D @playwright/test && npx playwright install chromium`).
- Decide on Python or Node to match the project; for a one-off check, a short standalone script in a temp dir is fine.

## Start the server
- Find the dev command in README or package.json (`npm run dev`, `python -m app`, `make run`).
- Start it in the background with output to a log file, record the PID, and wait for readiness by polling the URL, not with a fixed sleep:
  `until curl -sf http://localhost:3000 >/dev/null; do sleep 1; done` (with a timeout, and stop if the process dies).
- If a server is already running on the port, check whether it is the user's before reusing or stopping it.
- Stop only the server you started when finished.

## Write the flow
Python sketch:
```python
from playwright.sync_api import sync_playwright, expect
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page()
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto("http://localhost:3000/login")
    page.get_by_label("Email").fill("test@example.com")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Dashboard")).to_be_visible()
    assert not errors, errors
    b.close()
```
- Locate by role, label and visible text (`get_by_role`, `get_by_label`, `get_by_text`), then test ids. Avoid brittle CSS chains and XPath.
- Use web-first assertions (`expect(...).to_be_visible()`, `to_have_text`, `to_have_url`) which wait automatically. Do not add `wait_for_timeout` sleeps.
- For unknown pages, first take a screenshot or dump `page.content()` after `wait_for_load_state("networkidle")` to discover selectors, then write the flow.
- Cover the happy path, one key error path, and the specific change being verified. Check narrow viewports if layout changed (`page.set_viewport_size`).

## On failure
- Save a full-page screenshot (`page.screenshot(path=..., full_page=True)`), the console errors, failed network requests (`page.on("requestfailed")`) and the server log tail.
- Look at the screenshot before guessing. Distinguish app bug, test bug (wrong selector, timing) and environment (server not ready, port clash).
- For flaky steps, find the missing wait condition; never retry until green.

## Report
Flows run, assertions checked, pass or fail, screenshot paths, console errors seen, and the server command used.
