# Legacy tests (frozen)

Every test written before the November 2026 redesign: pytest modules (many on
SQLite), Node unit suites and the Playwright browser suites. They are **not
maintained and not run by default**. The core suite in `tests/core` replaces
them area by area.

- Do not add tests here and do not fix them. A legacy test that is in your way
  gets deleted; a behaviour worth keeping gets a new test in `tests/core`.
- Run on demand only: `scripts/test.sh legacy [path…]` (pytest),
  `node --test tests/legacy/*.test.cjs`, `scripts/run_browser_tests.sh`.
- Once an area is ported, its legacy files are deleted.
