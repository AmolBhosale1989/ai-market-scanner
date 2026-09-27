# Gate 3 production DAG

Both lanes retain the Stage 70 saved T0/pg_snapshot boundary and the fail-closed
Stage 72 catalyst evidence gate. They run broad discovery, premarket, theme and
sector analysis concurrently. V3 starts after broad discovery and premarket.
Momentum starts after broad discovery and sector rotation; order flow consumes
momentum, so these two stages cannot safely launch together. This chain runs
concurrently with V3.

After V3, theme and order flow complete, the full lane runs daily finalization
(Stage 135, previously Stage 75) with `--defer-publication`. It still generates
daily/legendary candidates, watchlists and health, but cannot overwrite V3
recommendations or assemble a partial product feed.

Both lanes then run `scanner.strategy_finalize` (Stage 138). It requires PASS
records for V3, sector rotation, momentum, order flow and theme from the current
production run (also daily_scan in the full lane). It validates the intraday
output metadata against the saved T0, snapshot and run ID, including empty output,
and reads hash-verified current-run rows. Only then does the existing V3
eligibility selector write recommended_trades, with centrally enforced provenance.
No strategy scoring or order-flow veto rule is changed by this orchestration PR.

V3's production `--defer-finalization` path never writes recommendations/product
feeds or sends Telegram notifications before the join. State, journals and monitor
outputs remain run-scoped inputs for later stages. Standalone CLI behavior stays
available without the deferral flag. Product assembly follows downstream validation,
challenger, performance and discovery work. The publication head moves only after
production audit, actual-run acceptance and actual-time freshness checks.

The full acceptance contract now uses an explicit dependency graph rather than
a linear list. Stage numbers are telemetry labels, not dependency definitions.

## Deployment status

PR 66 merged at 9c3fafa02cf1ee0517dba77566e6341a7f6aee01. Production application of
migrations 006/007 and a fresh snapshot baseline remain to be verified. The
production workflow already invokes `scanner.control_plane migrate` before
creating a new production run. A fresh run must use main after PR 66, not rerun an
old Actions job pinned to an earlier commit. Pre-migration snapshots are not a
valid new baseline. CI databases do not constitute production migration evidence.
