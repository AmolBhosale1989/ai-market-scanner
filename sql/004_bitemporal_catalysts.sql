-- Bitemporal catalyst facts. Knowledge-time revisions share warehouse provenance.
CREATE TABLE IF NOT EXISTS warehouse_catalyst (
    catalyst_revision_id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id) ON DELETE RESTRICT,
    ticker TEXT NOT NULL,
    catalyst_type TEXT NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    known_from TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    known_to TIMESTAMPTZ,
    warehouse_run_id UUID NOT NULL REFERENCES warehouse_run_log(warehouse_run_id) ON DELETE RESTRICT,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash)=64),
    payload JSONB NOT NULL,
    CHECK (known_to IS NULL OR known_to >= known_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_warehouse_catalyst_active
ON warehouse_catalyst(provider,provider_event_id,ticker)
WHERE known_to IS NULL;

CREATE INDEX IF NOT EXISTS ix_warehouse_catalyst_pit
ON warehouse_catalyst(ticker,event_timestamp,known_from,known_to);

CREATE INDEX IF NOT EXISTS ix_warehouse_catalyst_run
ON warehouse_catalyst(warehouse_run_id);

CREATE INDEX IF NOT EXISTS ix_warehouse_catalyst_instrument
ON warehouse_catalyst(instrument_id);


-- Successful provider checks are evidence in their own right. Keeping them
-- separate prevents an empty-result proof from masquerading as a market event.
CREATE TABLE IF NOT EXISTS catalyst_check (
    catalyst_check_id BIGSERIAL PRIMARY KEY,
    provider TEXT NOT NULL,
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id) ON DELETE RESTRICT,
    ticker TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    warehouse_run_id UUID NOT NULL REFERENCES warehouse_run_log(warehouse_run_id) ON DELETE RESTRICT,
    result_status TEXT NOT NULL CHECK (result_status IN ('EVENTS','NO_EVENT','PROVIDER_PAYLOAD_REJECTED')),
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    CHECK ((result_status='NO_EVENT' AND event_count=0 AND rejected_count=0) OR
           (result_status='EVENTS' AND event_count>0 AND rejected_count=0) OR
           (result_status='PROVIDER_PAYLOAD_REJECTED' AND rejected_count>0))
);
CREATE INDEX IF NOT EXISTS ix_catalyst_check_pit
ON catalyst_check(ticker,provider,checked_at DESC);
