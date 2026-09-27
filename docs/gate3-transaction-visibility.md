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

The amendment adds catalyst events/checks and the instrument catalogue to the
same visibility contract. Catalyst readers rank visible revisions before applying
the event window; mutable known_to closures cannot erase an older visible event.
Stage 72 and final acceptance use the saved visibility predicate.

Instrument INSERT/UPDATE/DELETE operations append immutable catalogue revisions,
including deletion tombstones. Joins first choose each instrument's visible
revision, then match its symbol, so a later rename cannot alter old joins.
Instrument dimension history and catalyst-check records also carry protected
writer_xid values. Direct fact mutation is rejected; only catalyst known_to
closure is permitted and snapshot readers do not depend on that mutable field.

At capture, a REPEATABLE READ transaction freezes the master_universe,
live_universe and tradable_universe dataset payloads and their verified hashes.
The capture query and catalogue reads share the same database snapshot. Consumers
read those immutable copies by (run, T0, pg_snapshot, dataset), and acceptance
rejects rewritten current catalogue outputs. Published universe reads used by
independent ingestion retain their publication-based behavior.

Migration 007 establishes conservative baselines for existing catalyst/catalogue
rows and adds triggers/history tables. Original pre-migration metadata revisions
cannot be recovered. Snapshots made before catalogue freezing require a fresh
run. DAG ordering is unchanged. Corporate-action calculation correctness is not
established merely by versioning a dimension record.

## Verification

PostgreSQL tests hold inserts uncommitted while capturing T0, then commit or roll
back and compare the original snapshot reads. Cases include savepoints,
state revisions, OHLCV corrections, coverage queries, fresh snapshots and VACUUM
FREEZE. Unit tests reject missing/malformed/mismatched visibility and verify the
gate passes one captured pair to every tier.


Additional PostgreSQL tests cover late catalyst corrections and clean-check
batches, rollback, ticker rename/currency/active-status changes, deletion
tombstones, ON CONFLICT inserts, immutable dimension records, and a concurrent
universe rewrite during capture. The acceptance fixture commits all evidence
before capturing its real snapshot, rather than relying on backdated inserts.
