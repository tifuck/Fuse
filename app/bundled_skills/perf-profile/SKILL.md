---
name: perf-profile
description: Find and fix a performance problem with measurements, not guesses. Use when something is slow, uses too much CPU or memory, a query or page load regressed, or the user asks to optimize or profile code.
metadata:
  category: Quality
---
# Performance profiling

Measure, fix the single biggest cost, measure again. No optimization without a before and after number.

## 1. Define the problem
- What is slow, for which input, on which machine, and what is the target (p95 under 200 ms, job under 5 min, RSS under 1 GB)?
- Build a repeatable reproduction: a script, a benchmark, or a request you can run many times. Use realistic data sizes.
- Record a baseline: run it several times and note the median and spread. Warm caches the same way each run.

## 2. Profile, do not guess
Pick the tool for the layer:
- Python: `py-spy record -o profile.svg -- python app.py` or `py-spy top --pid <pid>` for a running process; `python -m cProfile -s cumtime script.py`; `tracemalloc` for memory.
- Node: `node --cpu-prof app.js` then open the .cpuprofile in Chrome devtools; `--heap-prof` for memory.
- Go: `go test -bench . -cpuprofile cpu.out` then `go tool pprof -top cpu.out`; `net/http/pprof` for servers.
- Rust/C/C++: `perf record -g ./bin` then `perf report`; flamegraphs.
- JVM: async-profiler or JFR.
- Database: `EXPLAIN (ANALYZE, BUFFERS)` in Postgres, `EXPLAIN ANALYZE` in MySQL; slow query log; count queries per request to spot N+1.
- Browser: devtools Performance and Network panels, Lighthouse; look at long tasks, layout thrash, bundle size, waterfall.
- System: `time`, `/usr/bin/time -v` for peak memory, `iostat`, `vmstat` to tell CPU-bound from IO-bound.
Attach to production only with the user's approval, and prefer sampling profilers there.

## 3. Read the profile
- Sort by inclusive (cumulative) time to find the expensive subtree, then by self time to find the hot function.
- Classify the cost: algorithmic (quadratic loop, repeated work), IO wait (sequential network calls, missing index, N+1), allocation/GC, serialization, lock contention.
- Ignore anything under a few percent of total; it is not the problem.

## 4. Fix the top cost
Typical fixes, in rough order of payoff:
- Better algorithm or data structure (set/dict lookup instead of list scan).
- Remove repeated work: hoist out of loops, cache pure results, batch queries, add the missing index.
- Do IO concurrently or in bulk instead of one by one.
- Stream instead of loading everything into memory.
- Only then micro-optimizations.
Change one thing at a time and keep behaviour identical; run the existing tests.

## 5. Re-measure
Run the same benchmark the same way. Report baseline, after, and the delta. If the gain is within noise, revert the change. Repeat from step 2 if the target is not met.

## Report
```
Problem: <what, input size>
Baseline: <median, spread>  After: <median, spread>
Cause: <profile evidence>
Fix: <change>
Verified: <benchmark and tests run>
```
