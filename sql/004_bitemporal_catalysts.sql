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
