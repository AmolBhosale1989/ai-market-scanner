# Catalyst ingestion handoff

Run 49 reached the database-only Stage 72 gate and failed 2,757 required
symbol/provider checks. That number does not identify 2,757 committed rows or
prove late-commit exclusion. The existing catalyst_pipeline had no network
calls. The independent data-plane workflow reads the last published universe,
which can differ from the current 1,015-symbol structural plan.

The production sequence is now:

- 60: intraday price ingestion.
- 65: catalyst_pipeline --ingest-only reads the current run's live_universe,
  launches the three existing provider collectors concurrently, and waits for
  every collector to return after its database commits.
- 70: warehouse_gate captures the timestamp and PostgreSQL snapshot together,
  checks prices, persists the boundary, and logs both values.
- Export WAREHOUSE_CONSUMER_SNAPSHOT=1.
- 72: catalyst_pipeline --verify-only performs the existing database-only
  coverage check against that boundary. Default invocation remains verify-only.
- Existing parallel strategy execution, producer join, then 135/138 finalization.

The stage acceptance contract includes the new ingestion dependency in both
full and live modes. Ingest-only rejects consumer snapshot mode and requires
an active production run. Provider imports are lazy and exclusive to ingestion.
The independent scheduled worker retains its published-universe default.

A completed Stage 65 means collection attempts completed, not that coverage
passed. Existing workers preserve valid partial results and report failures;
Stage 72 alone evaluates all required ticker/provider checks. Yahoo and SEC
retain the 15-minute maximum age, Alpha Vantage 24 hours. No rejected or failed
fetch is converted into a successful no-event check.

Operational limit: provider work is now a production prerequisite bounded by a
30-minute timeout. A long collection can age out early checks or price arrivals;
provider failures, missing SEC mappings, and stale data still block execution.
This is a correctness handoff, not a claim that provider throughput is solved.
No production run is dispatched by this PR.
