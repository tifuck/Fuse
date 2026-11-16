---
name: property-tests
description: Write property-based tests with Hypothesis, fast-check or proptest. Use for parsers, serializers, encoders, data structures, math or any pure logic where example tests miss edge cases, or when asked for fuzz-style tests.
metadata:
  category: Quality
---
# Property-based tests

Describe what must hold for all inputs, let the library generate inputs, and keep the shrunk counterexamples.

## Pick the library the stack already uses
- Python: Hypothesis (`from hypothesis import given, strategies as st`).
- JS/TS: fast-check (`fc.assert(fc.property(...))`).
- Rust: proptest (`proptest! { ... }`). Go: `testing/quick` or rapid. JVM: jqwik.
If none is installed, ask before adding it as a dev dependency.

## Find properties
Work through these patterns; most code fits at least one:
1. **Round-trip**: `decode(encode(x)) == x` (serialize/parse, compress, escape/unescape).
2. **Invariant**: something always true of the output (sorted output is ordered and a permutation of input; balance never negative; length preserved).
3. **Idempotency**: `f(f(x)) == f(x)` (normalize, format, dedupe, upsert).
4. **Oracle equivalence**: fast implementation matches a slow obvious one (`my_sort(xs) == sorted(xs)`).
5. **Metamorphic**: a known change to input gives a predictable change to output (adding a filter never grows the result; scaling input scales output).
6. **No crash**: any input in the valid domain returns or raises the documented error, never an unexpected exception.
Avoid restating the implementation inside the test; that only checks the code equals itself.

## Generators
- Build inputs from the domain, not from raw bytes: composite strategies for valid objects, plus a separate test for invalid inputs.
- Include the awkward cases: empty, one element, duplicates, unicode, negative, zero, max int, NaN where relevant.
- Prefer constructing valid data over filtering (`assume`, `.filter`); heavy filtering makes generation slow and weak.
- Keep each example fast so the default run count (about 100) stays in seconds.

## When it fails
1. Read the shrunk counterexample; it is usually minimal already.
2. Decide whether the code or the property is wrong. A wrong property is fixed by tightening the domain or the claim, with a note why.
3. Add the counterexample as an explicit example test (`@example(...)` in Hypothesis, a plain unit test elsewhere) so it runs forever regardless of the random seed.
4. Fix the code, rerun the property and the example.

## Reproducibility
- Print or record the seed on failure (fast-check reports `seed` and `path`; Hypothesis keeps a local example database; proptest writes `proptest-regressions/` files, which should be committed).
- In CI, use a fixed or derandomized profile if flakiness from random generation is a concern, and a larger example count in a nightly job if available.

## Report
List each property, its pattern, and any counterexample found and how it was fixed.
