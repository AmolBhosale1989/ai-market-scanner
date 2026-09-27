# Structural planning before the consumer boundary

Run 48 failed at Stage 40 because --prepare-only called Catalyst enrichment before
the final consumer boundary was enabled. Enabling the Stage 30 snapshot would
mix preparation and strategy boundaries and exclude later intraday ingestion.

Stage 40 now performs only the daily liquidity/price prefilter. It persists the
tradable catalogue, a ticker-only daily_structural_universe and the live_universe
ingestion plan, then returns. It does not rank themes, analyze technical setups,
enrich events/Catalysts, score recommendations or write daily_prepared outputs.
Stage 50 preserves all structural identities when rebuilding the live plan;
its former dependency on final_score is removed.

The full plan conservatively retains all structurally tradable names, including
names beyond the normal 420 ranked slots. This increases intraday ingestion work
relative to the former 420-plus-selected-candidates plan (the observed run had
1,015 tradable names). Existing coverage/freshness gates and timeouts stay intact.
This avoids assuming that the eventual T0 scoring order was known before T0.

Stage 60 ingests that plan. Stage 70 captures T0/pg_snapshot and freezes catalogues.
Stage 72 validates Catalyst evidence. V3 and the sector/momentum/order-flow branch
then execute; the existing producer barrier remains unchanged.

Daily finalization is Stage 135 since PR 67, not the former Stage 75. Its
--finalize-prepared handler now requires both anchor and snapshot before reads,
loads the frozen live and master catalogues, and rebuilds technical inputs from
warehouse data under that boundary. Only planned identities are evaluated; no
pre-T0 scores or prior daily_prepared rows are reused. Event/Catalyst enrichment,
final scoring and daily_prepared writes occur here, with boundary metadata.
Finalization does not overwrite frozen catalogues or the theme_live product.
Stage 138 retains exclusive production V3 recommendation ownership.

This PR does not merge, dispatch another baseline, change strategy formulas or
relax any failure gate. Production throughput and the final snapshot/join still
require a new acceptance run after review.
