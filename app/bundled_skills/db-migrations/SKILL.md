---
name: db-migrations
description: Write safe database schema migrations using expand/contract, batched backfills, lock awareness and a rollback plan. Use when adding, renaming or dropping columns, tables or indexes, changing types, or backfilling data.
metadata:
  category: Build
---
# Database migrations

A migration must be safe to run while the old and new versions of the app are both live, and safe to run twice.

## Before writing
- Use the project's migration tool (Alembic, Django, Rails, Prisma, Flyway, golang-migrate, Knex). Generate the file with it; do not hand-edit applied migrations.
- Check table sizes and traffic for what you touch. A rewrite of a 10-row table and a 100M-row table are different problems.
- Never run migrations against production or a shared database yourself. Test locally or on a copy, and leave applying to the user.

## Expand / contract
Split risky changes into deployable phases:
1. **Expand**: add the new column or table, nullable or with a default, no constraints that existing rows violate. App writes to both old and new.
2. **Backfill**: copy data in batches (below).
3. **Switch**: app reads from the new shape. Add NOT NULL or constraints once data is complete.
4. **Contract**: in a later release, after nothing reads the old shape, drop it.
A rename is add-new + backfill + switch + drop-old, never a direct `RENAME` while old code is running.

## Locks (Postgres specifics; check equivalents for your engine)
- Set `lock_timeout` (for example `SET lock_timeout = '5s'`) so a migration waiting on a lock fails fast instead of blocking all traffic behind it.
- Create indexes with `CREATE INDEX CONCURRENTLY` (outside a transaction; most tools need a flag for that).
- Add foreign keys and check constraints as `NOT VALID`, then `VALIDATE CONSTRAINT` separately.
- Adding a column with a constant default is cheap on Postgres 11+; volatile defaults and type changes rewrite the table.
- For NOT NULL on a big table, add a `CHECK (col IS NOT NULL) NOT VALID`, validate it, then set NOT NULL.
- MySQL: check whether the ALTER is online (`ALGORITHM=INPLACE, LOCK=NONE`) or use the project's online schema change tool.

## Backfills
- Separate from schema changes; run as a script or data migration.
- Batch by primary key range (for example 1,000 to 10,000 rows), commit each batch, pause briefly between batches if load matters.
- Make it resumable and idempotent: `WHERE new_col IS NULL` or track the last processed id.
- Log progress; estimate total time from the first batches.

## Idempotency and rollback
- Use `IF NOT EXISTS` / `IF EXISTS` where the tool allows, so a partial failure can be rerun.
- Write the down migration, or state clearly why the step is irreversible (dropped data). Dropping a column or table is destructive: confirm with the user and make sure a backup exists.
- Keep data-destroying steps in their own migration, last.

## Test
1. Apply on a fresh database (all migrations from zero).
2. Apply on a copy with realistic data and time it.
3. Run down then up again.
4. Run the app tests against the migrated schema, and the old app version if doing expand/contract.

## Report
Phases, lock risk per statement, expected duration, rollback plan, and what was tested on which data.
