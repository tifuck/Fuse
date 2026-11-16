---
name: tdd
description: Test-driven development in small red-green-refactor cycles. Use when implementing a feature or fixing a bug where behaviour can be pinned by a test, or when the user asks for TDD or test-first work.
metadata:
  category: Build
  inspired-by: obra/superpowers
---
# Test-driven development

Write the test before the code, one behaviour at a time. A test you never saw fail proves nothing.

## Before starting
- Find the existing test runner, layout and naming (`tests/`, `*_test.go`, `*.spec.ts`). Follow them.
- Find how to run a single test fast (`pytest path::name`, `npx vitest run file -t name`, `go test -run Name ./pkg`, `cargo test name`).
- List the behaviours you need as short sentences. Pick the simplest one first.

## The cycle
1. **Red.** Write one test for the next behaviour. Name it after the behaviour, not the function (`rejects_expired_token`, not `test_validate2`). Assert on observable output or state.
2. **Watch it fail.** Run only that test. Confirm it fails for the expected reason: an assertion about the missing behaviour, not an import error, typo or broken fixture. If it passes already, either the behaviour exists or the test is wrong; find out which.
3. **Green.** Write the least production code that makes it pass. Hard-coding is acceptable at this step if the next test will force the general case.
4. **Run the related tests.** Nothing previously green may go red.
5. **Refactor.** Remove duplication and improve names in both code and tests while everything stays green. No new behaviour in this step.
6. Repeat with the next behaviour.

## Bug fixes
Reproduce the bug as a failing test first, at the lowest level that shows it. Keep that test after the fix as the regression guard.

## Good tests
- One behaviour per test. If the name needs "and", split it.
- Before writing an assertion, name the production change that would make it fail. If none would, the test is useless.
- Test real behaviour. Mock only slow or external boundaries (network, clock, filesystem when needed), and never assert that a mock was set up the way you set it up.
- Keep test-only helpers in test code, not in production classes.
- Deterministic: fix seeds, inject clocks, no sleeps, no order dependence.
- Fast: the inner loop should take seconds.

## Rules
- Do not write production code for behaviour no failing test demands.
- Do not change a test to match buggy code. If a test was wrong, fix it in its own step and say why.
- Never skip, delete or loosen a failing test to get green.
- If code was already written without a test, write the test, then temporarily break the code to watch the test fail, then restore it.
- At the end, run the full relevant suite once and report which tests were added and what ran.
