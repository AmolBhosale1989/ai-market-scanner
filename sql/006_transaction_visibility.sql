-- Legacy rows become a conservative baseline visible to new snapshots only.
-- Old timestamp-only manifests cannot reconstruct historical commit visibility.
ALTER TABLE market_observation ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE state_document ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();

CREATE FUNCTION immutable_snapshot_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        RAISE EXCEPTION 'SNAPSHOT_INPUT_APPEND_ONLY: %', TG_TABLE_NAME;
    END IF;
    -- Always the full top-level XID, including when INSERT runs in a savepoint.
    NEW.writer_xid := pg_current_xact_id();
    RETURN NEW;
END;
$$;
CREATE TRIGGER market_observation_visibility
BEFORE INSERT OR UPDATE OR DELETE ON market_observation
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();
CREATE TRIGGER state_document_visibility
BEFORE INSERT OR UPDATE OR DELETE ON state_document
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();
