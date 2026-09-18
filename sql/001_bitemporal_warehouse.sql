-- Bitemporal Market Hunt warehouse.
-- PostgreSQL is authoritative; CSV/dashboard files are downstream exports only.

CREATE TABLE IF NOT EXISTS warehouse_run_log (
    warehouse_run_id UUID PRIMARY KEY,
    provider TEXT NOT NULL,
    request_type TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('STARTED','AVAILABLE','FAILED','PARTIAL')),
    request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_detail TEXT
);

CREATE TABLE IF NOT EXISTS instrument (
    instrument_id BIGSERIAL PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT 'EQUITY',
    exchange TEXT,
    currency TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (canonical_symbol, exchange)
);

-- Changing metadata is itself point-in-time data.
CREATE TABLE IF NOT EXISTS instrument_dimension_history (
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    attribute_name TEXT NOT NULL,
    attribute_value JSONB NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    warehouse_run_id UUID NOT NULL REFERENCES warehouse_run_log(warehouse_run_id),
    PRIMARY KEY (instrument_id, attribute_name, event_timestamp, ingested_at, warehouse_run_id)
);

CREATE TABLE IF NOT EXISTS market_observation (
    observation_id BIGSERIAL,
    instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
    data_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    warehouse_run_id UUID NOT NULL REFERENCES warehouse_run_log(warehouse_run_id),
    provider TEXT NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume NUMERIC,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    quality_status TEXT NOT NULL DEFAULT 'OK',
    PRIMARY KEY (observation_id, event_timestamp),
    UNIQUE (instrument_id, data_type, timeframe, event_timestamp, ingested_at, warehouse_run_id)
) PARTITION BY RANGE (event_timestamp);

CREATE INDEX IF NOT EXISTS ix_market_obs_pit
ON market_observation (instrument_id, data_type, timeframe, event_timestamp DESC, ingested_at DESC);

CREATE INDEX IF NOT EXISTS ix_market_obs_run
ON market_observation (warehouse_run_id);

CREATE TABLE IF NOT EXISTS market_observation_default
PARTITION OF market_observation DEFAULT;

-- Point-in-time helper: caller MUST bind :as_of. The lateral ranking selects
-- the last version that the system actually knew at that instant.
-- Application code generates the symbol/timeframe/data_type predicates.
