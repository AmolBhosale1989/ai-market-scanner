# Draft: independent ingestion and snapshot evaluation

PR #83 is merged at `72cb66a4cb0e57e2dac3865728fe0b29215444dd`.
Run #87 took 6m07s for the publish job and 6m11s for the workflow. It
published at 22:35:03 IST, but its oldest retained bars expired at 22:40.
This split removes Stage 60/65 from the Engine's critical path; it does not
establish a four-minute or continuous-freshness result before measurement.

## Execution

| Lane | Cycle | Ownership |
| --- | --- | --- |
| Feeder | Seed the published catalogue → Stage 60 prices → Stage 65 mandatory Alpha → committed receipt | Warehouse writes only; no consumer T0 and no publication |
| Engine | Seed daily artifacts → Stage 70 T0/pg_snapshot and feeder binding → Stage 72 → parallel strategies → joins → Stage 290 → atomic publication | One new immutable snapshot and production run ID per evaluation |
| Full production | Existing daily preparation, ingestion, strategies and publication | Establishes the daily baseline; remains available for rebuild/recovery |

`live_feeder.yml` and `live_engine.yml` are independent workflows. The Engine
and legacy/full production share `market-hunt-production` concurrency. The
Feeder has its own group. No cancellation of an in-flight publication or
parallel Engine cycles is allowed. Provider work can continue while consumers
evaluate a previous T0: writer_xid visibility excludes late commits.

Both jobs install dependencies once and run bounded 45-minute sessions.
Feeder target start spacing is 60 seconds, Engine target spacing 120 seconds.
If work exceeds its interval, the next cycle starts after it completes; missed
ticks do not create overlapping work or a backlog. Actual cadence can therefore
be longer than the target. Each cycle is a fresh subprocess with a new run ID
and fresh consumer caches. Feeder/Engine whole-cycle deadlines are 240/600
seconds, inside the existing HTTP, SQL and publication bounds. Failure stops
that session. Engine cancellation kills its parallel strategy process group.
NYSE calendar checks handle holidays, DST and early closes, and require ten
minutes remaining before starting a cycle. A stale daily baseline requires the
full lane; neither new lane silently promotes itself to a full rebuild.

GitHub scheduled workflows have a documented five-minute minimum and can be
delayed or dropped. The 15-minute offset cron entries are restart watchdogs,
not claims of a one-/two-minute scheduler. Runner replacement and failures can
still create gaps. See [GitHub scheduling documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

The first Engine attempt requires a completed, matching feeder receipt. If
provider startup is delayed, it fails closed and the next watchdog is a new
attempt. This startup/restart behavior must be included in availability tests.
The existing dependency set is retained: V4 options polling still executes in
the Engine. This draft removes price/Alpha ingestion, not every external I/O
operation or every package dependency. Further isolation is a separate change.

## Exact handoff

Migration 008 adds append-only `feeder_receipt` and `engine_feeder_binding`
tables, and an `INGESTED` non-publication run status. A receipt is committed
only after seed-plan, price ingestion and Alpha ingestion have all completed
and their catalogue hashes have been verified. It proves commits joined, not
provider coverage: Stage 70 and mandatory Alpha Stage 72 remain authoritative.

Within Stage 70's single repeatable-read boundary transaction, the Engine
freezes its catalogue and selects a receipt that:

- Was committed and visible in the exact saved PostgreSQL snapshot.
- Finished within ten minutes before T0.
- Uses the same source commit and all three exact catalogue hashes.

No receipt, mismatched catalogue/commit, corrupt receipt or late commit means
no saved Engine boundary. The receipt binding and frozen catalogue commit or
roll back together. Acceptance and publication independently validate that
binding against the saved snapshot, source commit and frozen catalogue.
The Engine DAG contains `engine_seed`, not fake PASS entries for Stage 60/65.
All existing critical coverage, Alpha evidence, downstream joins, hashes,
optional-symbol filtering and final ten-minute source-bar expiry remain.

## Activation and rollback

New schedules are disabled unless repository variable
`LIVE_PIPELINES_ENABLED` is exactly `true`. Manual dispatch defaults to ONE
cycle; no new workflow can dispatch a successor. A draft PR does not activate
the split, apply production migrations or authorize supervised runs.

Before activation:

GitHub does not register a new manual workflow until it exists on the default
branch. For draft-branch Feeder validation, choose `production.yml`, select
the draft branch and explicitly select `mode=feeder`. This calls that commit's
exact `live_feeder.yml` with `cycles: 1`; the publisher job is excluded.
It neither starts the Engine nor activates the scheduled split. Normal
`full` and `live` modes retain their existing behavior.

1. Verify CI including real PostgreSQL late-commit, immutable receipt and
   acceptance/publication tests.
2. During an active session, authorize a bounded Feeder run, then an Engine
   run on the exact same commit after a matching feeder receipt exists.
3. Validate multiple consecutive publication intervals against retained
   source-bar expiry, plus Render UI observations through the handoffs.
4. Activate the repository variable only after review. It disables the old
   scheduled live lane and old continuation while retaining the full daily
   schedule. Engine and full production use the same publication mutex.

Before changing deployed commits, restart both lanes together: receipts from
different source commits intentionally do not qualify. For rollback, disable
the variable, stop the two session jobs, then use the existing production live
lane. Already-published snapshots are immutable throughout. Avoid launching
full ingestion concurrently with a supervised Feeder to prevent duplicate
provider load; ordinary scheduled live work is suppressed at cutover.

## Required evidence

Report feeder request span, joined commit time and receipt ID; Engine T0 and
pg_snapshot; Stage 70 SQL, strategy/V4, Stage 290 and publication times; actual
job and per-cycle duration; oldest retained source-bar end/expiry; publication
ID; and Render state over consecutive handoffs. Do not sum overlapping spans.
One passing run or one sub-four-minute duration does not prove continuous
freshness. Source-bar age, cycle gaps and upstream availability still matter.
