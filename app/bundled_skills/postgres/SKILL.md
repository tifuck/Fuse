---
name: postgres
description: PostgreSQL practices for schema and index design, EXPLAIN ANALYZE, N+1 avoidance, transactions, locking, pooling and row-level security. Use when writing or reviewing SQL, designing tables, or debugging slow Postgres queries.
metadata:
  category: Build
  inspired-by: supabase/agent-skills
---
# PostgreSQL

Measure with the planner, design for the queries you actually run, keep transactions short.

## Schema
- Primary keys: `bigint generated always as identity`, or UUID (prefer time-ordered UUIDv7 for insert locality) when ids must be global or unguessable.
- Types: `text` over `varchar(n)` unless a limit is a real rule; `timestamptz` for time; `numeric` for money (or integer minor units); `jsonb` not `json`; `boolean` not int flags.
- Constraints in the database: NOT NULL by default, foreign keys, `CHECK`, `UNIQUE`. They are cheaper than bugs.
- Index every foreign key column you join or delete through; Postgres does not do it automatically.
- Use lower-case snake_case identifiers to avoid quoting.

## Indexes
- Build them for real `WHERE`, `JOIN` and `ORDER BY` patterns. Composite index column order: equality columns first, then range or sort.
- Partial indexes for hot subsets (`WHERE deleted_at IS NULL`), expression indexes for `lower(email)`.
- Covering indexes with `INCLUDE` for index-only scans on hot reads.
- GIN for `jsonb` containment, arrays and full-text; BRIN for huge append-only time series.
- Find unused indexes via `pg_stat_user_indexes` (idx_scan = 0) and remove them after checking replicas too; each index slows writes.

## Reading plans
- `EXPLAIN (ANALYZE, BUFFERS) <query>`; run it on realistic data. On writes, wrap in `BEGIN; ... ROLLBACK;` since ANALYZE executes the statement.
- Look for: Seq Scan on big tables with a selective filter, estimated vs actual rows off by 10x or more (run `ANALYZE table`, consider extended statistics), nested loops over large row counts, sorts spilling to disk.
- `pg_stat_statements` shows which queries cost the most in total; fix those first.

## Query patterns
- N+1: one query per row from application loops. Replace with a join, `WHERE id = ANY($1)`, or the ORM's eager loading.
- Always parameterize; never interpolate user input into SQL.
- Keyset pagination (`WHERE (created_at, id) < ($1, $2) ORDER BY created_at DESC, id DESC LIMIT 50`) instead of large OFFSET.
- Batch inserts (multi-row `VALUES`, `COPY`); use `INSERT ... ON CONFLICT` for upserts.
- Select only the columns you need.

## Transactions and locking
- Keep transactions short; no network calls or user waits inside them.
- Default READ COMMITTED; use `SELECT ... FOR UPDATE` (or `SKIP LOCKED` for job queues) when reading to then write.
- Lock rows in a consistent order to avoid deadlocks; retry on serialization failures (SQLSTATE 40001, 40P01).
- Set `statement_timeout` and `idle_in_transaction_session_timeout` for app roles.
- DDL takes strong locks; set `lock_timeout` and see the db-migrations practices for large tables.

## Connections
- Use a pool (PgBouncer in transaction mode, or the driver's pool). Size it to a small multiple of CPU cores on the server, not to request concurrency.
- In transaction pooling, avoid session state (session-level `SET`, advisory session locks, temp tables across transactions).

## Row-level security
- `ALTER TABLE t ENABLE ROW LEVEL SECURITY;` then explicit policies per command: `CREATE POLICY own_rows ON t FOR SELECT USING (owner_id = current_setting('app.user_id')::bigint);`
- Table owners and superusers bypass RLS unless `FORCE ROW LEVEL SECURITY`; test as the real app role.
- Index the columns used in policies; policies run on every row.

## Safety
Ask before running anything that writes, locks or deletes on a shared or production database. Prefer a local copy for experiments.
