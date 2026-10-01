# Live-cycle latency follow-up to Run 84

Run 84 published 109 datasets, but its 6m28s publish job left FINX's source
bar only 3m09s from expiry. Render subsequently blocked IBM's expired input.
This change does not claim continuous freshness or change any freshness limit.

## Changes

* Replace two process-local batch executors (five provider threads each) with
  one rolling ten-worker pool of independent `Ticker.history` requests. Keep
  bounded lookahead, deterministic database batch order, two attempts maximum,
  no rate-limit sleep, socket limits and the existing 120-second process deadline.
  Structured diagnostics report attempt, timeout, rate limiting and source bars.
* Match coverage's revision selection to the existing PIT index order. Share
  overlapping symbol reads within a timeframe and run at most two timeframe
  queries concurrently against the identical explicit T0/xid8 boundary.
* Bind complete per-symbol history/quality/freshness evidence into Stage 70's
  hashed manifest. Publication verifies the evidence hash, run, anchor, snapshot,
  frozen symbol set and tier policy, then evaluates every symbol at the actual
  publication clock. No cached PASS decision or global MAX(timestamp) shortcut.
  Old manifests retain the full SQL verification path. Missing/corrupt new
  evidence fails closed. The final signal expiry check inside publication remains.
* Buffer V4 cycle dataset outputs and write all versions/rows in one transaction
  using psycopg 3 set-based JSONB inserts. Batch database audit alerts as well.
  Existing operational event/state locking and idempotency remain independent.
  Per-batch telemetry separates acquisition, version upsert, row delete/insert,
  preparation, finalization, commit and release. Commit acknowledgement loss is
  labelled uncertain. Lock/statement limits are 3s/15s.

## Index investigation

Production already has `ix_market_obs_pit` on instrument, data type, timeframe,
event timestamp DESC and ingestion timestamp DESC, plus state/event key indexes.
No speculative index migration is added. A full-universe DISTINCT/aggregate
experiment on Run 84's frozen master catalogue returned 5,340 covered symbols
but took 31.06s and spilled approximately 919 MiB to temporary blocks; the
bounded per-instrument query took 21.96s in that observation. These read-only
measurements are not controlled speedup claims. The spilling query was rejected.

## Validation contract

CI must exercise PostgreSQL snapshot visibility, invalid-history coverage,
atomic dataset replacement/rollback, empty outputs and JSONB hash round trips,
plus bounded scheduling and rate-limit handling. Production proof requires
consecutive live runs of this commit: exact source-bar expiry versus the next
publication time, actual Gate 290/publication success, and sampled Render state.
One fast run and finite UI samples alone cannot prove uninterrupted freshness.
Do not infer a controlled saving by subtracting different historical workloads.
Draft-branch validation runs are supervised individually; automatic continuation
is limited to successful main-branch publications.

## Expired optional signal projection

Run 86 stopped at acceptance: SKYY's latest returned bar was already 9m02s
old at fetch time and expired before the strategy join. Its 5m30s failed job
did not include atomic publication and is not an end-to-end speed measurement.

Acceptance now excludes expired non-critical signal rows from unpublished
outputs, including sector rotation/themes and embedded product-feed copies.
The 21 core/rotation benchmark symbols retain fatal expiry checks. Missing
datasets/identities, invalid or future timestamps, original hash corruption and
failed producers remain fatal. The ten-minute boundary is unchanged.

Original dataset hashes are verified before filtering. Exclusions, original
hashes and the actual validation clock are recorded in version metadata. Row
replacement, updated hashes and publication manifests are transactional; a
failure rolls everything back. Source observations and T0/xid8 do not change.
Historical journals and already-published versions cannot be pruned.

The final publication transaction repeats filtering at its actual clock and
rebuilds the manifest if rows expired after acceptance. A bounded recheck
prevents filtering SQL time from hiding another expired row. Render continues
to enforce strict freshness on the actual retained payload; this does not keep
an old publication green indefinitely or prove consecutive-run overlap.
