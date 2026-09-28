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
logs. This patch does not batch those transactions or bound SQL execution time.
Socket inactivity limits are not total wall-clock limits (DNS, slow streams,
separate fallback requests, and database work remain possible delays). The
30-minute outer deadline remains. Production performance is still unproven.
