ALTER TABLE warehouse_catalyst ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE catalyst_check ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE instrument ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();
ALTER TABLE instrument_dimension_history ADD COLUMN writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id();

CREATE TABLE instrument_catalogue_revision (
    revision_id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    exchange TEXT,
    currency TEXT,
    active BOOLEAN NOT NULL,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    known_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id()
);
INSERT INTO instrument_catalogue_revision
(instrument_id,canonical_symbol,asset_class,exchange,currency,active)
SELECT instrument_id,canonical_symbol,asset_class,exchange,currency,active FROM instrument;
CREATE INDEX ix_instrument_catalogue_revision
ON instrument_catalogue_revision(instrument_id, revision_id DESC);
CREATE TRIGGER instrument_catalogue_immutable
BEFORE INSERT OR UPDATE OR DELETE ON instrument_catalogue_revision
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();

CREATE FUNCTION version_instrument_catalogue() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r instrument;
BEGIN
    IF TG_OP = 'DELETE' THEN r := OLD; ELSE
        r := NEW;
    END IF;
    INSERT INTO instrument_catalogue_revision
    (instrument_id,canonical_symbol,asset_class,exchange,currency,active,deleted)
    VALUES (r.instrument_id,r.canonical_symbol,r.asset_class,r.exchange,r.currency,r.active,TG_OP='DELETE');
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION stamp_instrument_writer() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.writer_xid := pg_current_xact_id(); RETURN NEW; END;
$$;
CREATE TRIGGER instrument_writer
BEFORE INSERT OR UPDATE ON instrument
FOR EACH ROW EXECUTE FUNCTION stamp_instrument_writer();
CREATE TRIGGER instrument_catalogue_version
AFTER INSERT OR UPDATE OR DELETE ON instrument
FOR EACH ROW EXECUTE FUNCTION version_instrument_catalogue();
CREATE TRIGGER instrument_dimension_visibility
BEFORE INSERT OR UPDATE OR DELETE ON instrument_dimension_history
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();
CREATE TRIGGER catalyst_check_visibility
BEFORE INSERT OR UPDATE OR DELETE ON catalyst_check
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();

-- The legacy ingestion code closes known_to in-place. Preserve immutable facts
-- and their insertion XID; snapshot readers select the latest VISIBLE revision
-- rather than trusting that mutable closing timestamp.
CREATE FUNCTION protect_catalyst_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.writer_xid := pg_current_xact_id(); RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.known_to IS NULL AND NEW.known_to IS NOT NULL
       AND (to_jsonb(NEW)-'known_to') = (to_jsonb(OLD)-'known_to') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'CATALYST_REVISION_IMMUTABLE';
END;
$$;
CREATE TRIGGER catalyst_revision_visibility
BEFORE INSERT OR UPDATE OR DELETE ON warehouse_catalyst
FOR EACH ROW EXECUTE FUNCTION protect_catalyst_revision();

CREATE TABLE frozen_catalogue (
    pipeline_run_id UUID NOT NULL REFERENCES pipeline_run(pipeline_run_id),
    as_of_utc TIMESTAMPTZ NOT NULL,
    pg_snapshot TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    records JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
    PRIMARY KEY(pipeline_run_id,as_of_utc,pg_snapshot,dataset_name)
);
CREATE TRIGGER frozen_catalogue_immutable
BEFORE INSERT OR UPDATE OR DELETE ON frozen_catalogue
FOR EACH ROW EXECUTE FUNCTION immutable_snapshot_input();
