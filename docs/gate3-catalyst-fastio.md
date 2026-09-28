# Catalyst transport bounds and optional coverage

Stage 65 reaches the HTTP clients through catalyst_data_plane ->
catalyst_existing_adapters -> v4/catalysts. catalyst_pipeline remains a DB-only
verifier unless explicitly invoked with --ingest-only.

Yahoo now calls the same tickerStream endpoint used by yfinance.get_news
with an explicit requests transport, a 3-second connect timeout and 5-second
read timeout. The pool remains capped at eight workers. Invalid response shapes
raise errors, never successful NO_EVENT evidence. No cookie/crumb fallback is
attempted; endpoints requiring those may report failures.

SEC direct and relay requests use the same socket bounds. Direct retries are
clamped to one retry (two total attempts) with at most one second of backoff;
relay requests have one attempt. SEC remains capped at five workers and retains
its existing rate limiter and validated fallback sources. Redirect following is
disabled on both providers. HTTP 429 and transient server errors and socket
failures have bounded retries; Retry-After cannot extend the backoff.

These are socket inactivity limits, not a whole-job wall-clock deadline. DNS,
slow trickle responses, the size of the universe, separate SEC fallback URLs,
and database writes can still extend Stage 65. Its existing outer timeout is
unchanged. This patch does not prove production ingestion will finish in minutes.

Gate 72 and the final acceptance check now require only Alpha Vantage catalyst
evidence (24-hour freshness). Missing/stale Yahoo or SEC evidence produces
provider-specific CATALYST_COVERAGE_WARNING counts. Those counts are missing
qualifying ticker/provider checks, not proof of absent events. Their 15-minute
thresholds still determine whether evidence is reported as available.

This explicitly reduces the catalyst coverage requirement. Price gates,
immutable T0/snapshot validation, catalogue visibility, event rejection rules,
and downstream negative-catalyst handling remain enforced. Missing optional
coverage does not create synthetic successful checks or imply there is no risk.
Production timing and downstream execution still require a new baseline run.
