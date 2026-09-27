# Gate 3 transaction visibility

Production warehouse gates capture `statement_timestamp()` and
`pg_current_snapshot()::text` in one SELECT. Both are stored in the version-2
warehouse snapshot. Historical gate execution requires the saved timestamp AND
snapshot; it cannot synthesize historical visibility from today's database.

OHLCV selection, coverage aggregation, publication freshness rechecks, and pinned
state reads apply `pg_visible_in_snapshot(writer_xid, %s::pg_snapshot)` BEFORE
choosing the latest revision. Event-time, knowledge-time and period filters remain
in force. Production consumers reject manifests without a visibility snapshot.
V3 output provenance carries that snapshot alongside T0, run ID and hashes.

## Why writer_xid instead of raw xmin

PostgreSQL xmin is xid (32-bit) and can contain a subtransaction ID. The visibility
function takes xid8 and does not correctly classify subtransaction IDs. A database
INSERT trigger records pg_current_xact_id(), the full top-level transaction ID,
including savepoint writes. This ordinary stored value survives VACUUM FREEZE.
The observation and state revision tables reject UPDATE/DELETE; corrections must
append revisions so old snapshots remain reproducible.

Reference: https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-PG-SNAPSHOT

## Migration and limits

Migration 006 takes table locks and rewrites the two tables to stamp existing rows
with the migration transaction ID. This is a conservative baseline for NEW
snapshots; it cannot recover the original visibility of legacy transactions.
Plan this migration around warehouse size and ingestion availability. Old
manifests without pg_snapshot fail closed and require a fresh run.

Replay requires retained rows and transaction identity from the same database
lineage. A snapshot string is not a portable database backup, not SET TRANSACTION
SNAPSHOT, and not a full historical MVCC image of arbitrary mutable tables.
Database restore/reinitialization or administrative TRUNCATE are outside this
contract. No one-year retention guarantee is created by this PR.

This PR covers OHLCV and pinned state. Catalyst event/check reads and mutable
control-plane universe/catalogue datasets still need their own visibility/version
contract audit before declaring the entire I/O boundary sealed. DAG ordering is
unchanged.

## Verification

PostgreSQL tests hold inserts uncommitted while capturing T0, then commit or roll
back and compare the original snapshot reads. Cases include savepoints,
state revisions, OHLCV corrections, coverage queries, fresh snapshots and VACUUM
FREEZE. Unit tests reject missing/malformed/mismatched visibility and verify the
gate passes one captured pair to every tier.
