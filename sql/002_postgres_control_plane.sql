-- Versioned PostgreSQL control, state, and publication plane.
-- OHLCV remains in the bitemporal market_observation tables created by 001.

CREATE TABLE IF NOT EXISTS pipeline_run (
    pipeline_run_id UUID PRIMARY KEY,
    mode TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    warehouse_as_of TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('STARTED','VALIDATING','PUBLISHED','FAILED')),
    error_detail TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS pipeline_stage (
    pipeline_run_id UUID NOT NULL REFERENCES pipeline_run(pipeline_run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED','PASS','FAILED','SKIPPED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    input_hash TEXT,
    output_hash TEXT,
    row_count BIGINT,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (pipeline_run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS dataset_version (
    dataset_version_id BIGSERIAL PRIMARY KEY,
    pipeline_run_id UUID NOT NULL REFERENCES pipeline_run(pipeline_run_id) ON DELETE CASCADE,
    dataset_name TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL CHECK (status IN ('WRITING','AVAILABLE','FAILED')),
    row_count BIGINT NOT NULL DEFAULT 0,
    content_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (pipeline_run_id, dataset_name)
);

CREATE TABLE IF NOT EXISTS dataset_row (
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id) ON DELETE CASCADE,
    row_ordinal BIGINT NOT NULL,
    entity_key TEXT,
    payload JSONB NOT NULL,
    PRIMARY KEY (dataset_version_id, row_ordinal)
);

CREATE INDEX IF NOT EXISTS ix_dataset_version_lookup
ON dataset_version (dataset_name, completed_at DESC)
WHERE status='AVAILABLE';

CREATE INDEX IF NOT EXISTS ix_dataset_row_entity
ON dataset_row (dataset_version_id, entity_key);

CREATE TABLE IF NOT EXISTS publication_snapshot (
    publication_snapshot_id BIGSERIAL PRIMARY KEY,
    pipeline_run_id UUID NOT NULL UNIQUE REFERENCES pipeline_run(pipeline_run_id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('VALIDATING','PUBLISHED','REVOKED')),
    published_at TIMESTAMPTZ,
    manifest_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS publication_dataset (
    publication_snapshot_id BIGINT NOT NULL REFERENCES publication_snapshot(publication_snapshot_id) ON DELETE CASCADE,
    dataset_name TEXT NOT NULL,
    dataset_version_id BIGINT NOT NULL REFERENCES dataset_version(dataset_version_id),
    PRIMARY KEY (publication_snapshot_id, dataset_name)
);

CREATE TABLE IF NOT EXISTS publication_head (
    mode TEXT PRIMARY KEY,
    publication_snapshot_id BIGINT NOT NULL REFERENCES publication_snapshot(publication_snapshot_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS state_document (
    namespace TEXT NOT NULL,
    document_key TEXT NOT NULL,
    revision BIGINT NOT NULL,
    pipeline_run_id UUID REFERENCES pipeline_run(pipeline_run_id),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, document_key, revision)
);

CREATE INDEX IF NOT EXISTS ix_state_document_latest
ON state_document (namespace, document_key, revision DESC);

CREATE TABLE IF NOT EXISTS event_record (
    namespace TEXT NOT NULL,
    event_key TEXT NOT NULL,
    pipeline_run_id UUID REFERENCES pipeline_run(pipeline_run_id),
    observed_at TIMESTAMPTZ,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, event_key)
);

CREATE INDEX IF NOT EXISTS ix_event_record_observed
ON event_record (namespace, observed_at DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS health_observation (
    health_observation_id BIGSERIAL PRIMARY KEY,
    pipeline_run_id UUID NOT NULL REFERENCES pipeline_run(pipeline_run_id) ON DELETE CASCADE,
    module_name TEXT NOT NULL,
    status TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_date DATE,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS ix_health_observation_run
ON health_observation (pipeline_run_id, module_name, observed_at DESC);

-- Repair duplicate legacy instruments before enforcing normalized uniqueness.
-- The observation/history rows are re-pointed without dropping distinct facts;
-- exact logical duplicates are removed first to preserve their constraints.
CREATE TEMP TABLE instrument_merge_map ON COMMIT DROP AS
SELECT instrument_id AS duplicate_id,
       min(instrument_id) OVER (
           PARTITION BY canonical_symbol, COALESCE(exchange, '')
       ) AS keeper_id
FROM instrument;

WITH ranked AS (
    SELECT observation.observation_id, observation.event_timestamp,
           row_number() OVER (
               PARTITION BY map.keeper_id, observation.data_type, observation.timeframe,
                            observation.event_timestamp, observation.ingested_at,
                            observation.warehouse_run_id
               ORDER BY (observation.instrument_id=map.keeper_id) DESC,
                        observation.observation_id
           ) AS duplicate_rank
    FROM market_observation observation
    JOIN instrument_merge_map map ON map.duplicate_id=observation.instrument_id
)
DELETE FROM market_observation observation
USING ranked
WHERE observation.observation_id=ranked.observation_id
  AND observation.event_timestamp=ranked.event_timestamp
  AND ranked.duplicate_rank>1;

UPDATE market_observation observation
SET instrument_id=map.keeper_id
FROM instrument_merge_map map
WHERE observation.instrument_id=map.duplicate_id
  AND map.duplicate_id<>map.keeper_id;

WITH ranked AS (
    SELECT history.ctid AS row_reference,
           row_number() OVER (
               PARTITION BY map.keeper_id, history.attribute_name,
                            history.event_timestamp, history.ingested_at,
                            history.warehouse_run_id
               ORDER BY (history.instrument_id=map.keeper_id) DESC,
                        history.ctid
           ) AS duplicate_rank
    FROM instrument_dimension_history history
    JOIN instrument_merge_map map ON map.duplicate_id=history.instrument_id
)
DELETE FROM instrument_dimension_history history
USING ranked
WHERE history.ctid=ranked.row_reference
  AND ranked.duplicate_rank>1;

UPDATE instrument_dimension_history history
SET instrument_id=map.keeper_id
FROM instrument_merge_map map
WHERE history.instrument_id=map.duplicate_id
  AND map.duplicate_id<>map.keeper_id;

DELETE FROM instrument duplicate
USING instrument_merge_map map
WHERE duplicate.instrument_id=map.duplicate_id
  AND map.duplicate_id<>map.keeper_id;

-- Prevent duplicate NULL-exchange instruments under concurrent ingestion.
CREATE UNIQUE INDEX IF NOT EXISTS ux_instrument_symbol_exchange_normalized
ON instrument (canonical_symbol, COALESCE(exchange, ''));
