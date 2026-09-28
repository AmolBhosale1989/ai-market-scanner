# Alpha Vantage transport limits and ingestion visibility

The Stage 65 orchestrator in catalyst_pipeline delegates HTTP to
catalyst_data_plane and v4/catalysts. Alpha Vantage previously made one request
with a 20-second socket timeout; it had no quota loop or recursive retries.
SEC has a finite fallback chain (submissions, search, relay, Nasdaq), with
bounded retries per URL. No unbounded retry loop was found in either path.

Alpha Vantage now uses 3-second connect / 5-second read inactivity timeouts,
disabled redirects, and at most two total attempts (one retry). Transient
connection failures and server errors get one second of backoff. HTTP 429 fails
immediately without honoring Retry-After. SEC also fails each 429 fetch
immediately; its existing finite alternative-source chain remains intact.
Provider failure never creates a successful quiet check. Alpha Vantage remains
required by Gate 72, including its existing freshness and snapshot predicates.

HTTP errors are logged by status and exception class without request URLs or
API keys. Provider start/duration, SEC and Alpha fetch boundaries, and persistence
progress distinguish HTTP work from database writes on the next run. Progress
counts advance only after the evidence write and warehouse-run completion return.

Run #51 does not establish an Alpha HTTP hang. Alpha downloads a global calendar
once, then writes evidence for each ticker with start/write/finish database calls;
SEC also persists successful results ticker by ticker. Both lacked phase progress
logs. The persistence update below replaces those per-ticker commits with one bounded transaction.
Socket inactivity limits are not total wall-clock limits (DNS, slow streams,
separate fallback requests, and database work remain possible delays). The
30-minute outer deadline remains. Production performance is still unproven.

## Atomic bounded Alpha persistence (Run #52 follow-up)

Alpha's entire calendar batch now shares one PostgreSQL transaction: warehouse
run rows, event revisions, and successful checks all commit together. Each
progress message counts staged tickers; only the final message means committed.
No failed-run metadata is written on a separate unbounded cleanup connection.

Pool acquisition is explicitly capped at 5 seconds (the shared pool already
bounds physical connection attempts at 15 seconds). Once acquired, transaction-
local lock_timeout=3s and statement_timeout=15s protect every SQL statement.
A 120-second monotonic persistence budget includes acquisition and commit. A
watchdog cancels active SQL with a bounded 2-second cancellation request and
closes/discards the connection; sequential short queries cannot reset this
budget. The watchdog is joined before the connection is returned to the pool.
Cancellation/cleanup can add up to approximately 2 seconds and scheduling delay;
this is not a real-time OS guarantee. Network fetching occurs before this budget.

Closing an uncommitted session causes PostgreSQL to roll back the entire batch.
A transport/deadline failure during COMMIT is explicitly COMMIT_UNCERTAIN: a
client cannot prove rollback after losing a commit acknowledgment. No completion
marker is emitted in that case; the atomic database transaction cannot expose a
partially committed ticker batch. Gate 72's mandatory Alpha checks are unchanged.

Failure logs classify pool/connection, lock, statement, overall deadline,
deadlock, or other database errors without SQL parameters or credentials.
Tests exercise real PostgreSQL lock contention, pg_sleep cancellation, cumulative
short-query deadlines, complete rollback after a later ticker fails, and timeout
settings not leaking into the next pooled transaction.
