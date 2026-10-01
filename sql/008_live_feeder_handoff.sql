-- A completed ingestion cycle is evidence of joined commits, not coverage.
ALTER TABLE pipeline_run DROP CONSTRAINT pipeline_run_status_check;
ALTER TABLE pipeline_run ADD CONSTRAINT pipeline_run_status_check
    CHECK (status IN ('STARTED','VALIDATING','PUBLISHED','FAILED','INGESTED'));

CREATE TABLE feeder_receipt (
    pipeline_run_id UUID PRIMARY KEY REFERENCES pipeline_run(pipeline_run_id),
    completed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    writer_xid XID8 NOT NULL DEFAULT pg_current_xact_id(),
    source_commit TEXT NOT NULL,
    catalogue_hashes JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX ix_feeder_receipt_latest ON feeder_receipt(completed_at DESC);

CREATE TABLE engine_feeder_binding (
    pipeline_run_id UUID PRIMARY KEY REFERENCES pipeline_run(pipeline_run_id),
    feeder_run_id UUID NOT NULL REFERENCES feeder_receipt(pipeline_run_id),
    as_of_utc TIMESTAMPTZ NOT NULL,
    pg_snapshot TEXT NOT NULL,
    receipt_hash TEXT NOT NULL
);

CREATE FUNCTION protect_live_handoff() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'LIVE_HANDOFF_IMMUTABLE';
END;
$$;
CREATE TRIGGER immutable_feeder_receipt BEFORE UPDATE OR DELETE ON feeder_receipt
    FOR EACH ROW EXECUTE FUNCTION protect_live_handoff();
CREATE TRIGGER immutable_engine_feeder_binding BEFORE UPDATE OR DELETE ON engine_feeder_binding
    FOR EACH ROW EXECUTE FUNCTION protect_live_handoff();
