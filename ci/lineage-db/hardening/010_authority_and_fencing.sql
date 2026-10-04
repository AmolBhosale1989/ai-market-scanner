-- Candidate validation-only migration. Original migration 009 stays byte-identical.
-- These are intraday authority bindings, not a replacement for producer instrumentation.
CREATE TABLE lineage_draft.signal_authority (
 pipeline_run_id uuid NOT NULL,
 producer_hash text NOT NULL,
 signal_node_id text NOT NULL,
 dataset_name text NOT NULL,
 row_text text NOT NULL CHECK(jsonb_typeof(row_text::jsonb)='object'),
 row_hash text NOT NULL CHECK(row_hash=lineage_draft.sha256_text(row_text)),
 source_expires_at timestamptz NOT NULL,
 PRIMARY KEY(pipeline_run_id,producer_hash,signal_node_id),
 FOREIGN KEY(pipeline_run_id,producer_hash) REFERENCES lineage_draft.artifact(pipeline_run_id,content_hash)
);
CREATE TRIGGER immutable_signal_authority BEFORE UPDATE OR DELETE ON lineage_draft.signal_authority
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_signal_authority BEFORE TRUNCATE ON lineage_draft.signal_authority
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();

CREATE FUNCTION lineage_draft.validate_authority_view() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; ref jsonb; p lineage_draft.artifact; raw jsonb; o public.market_observation;
BEGIN
 b := (NEW.document_text::jsonb)#>'{body}';
 IF b->>'authority_schema' IS DISTINCT FROM 'LIVE_VIEW_V1' THEN
   RAISE EXCEPTION 'LINEAGE_AUTHORITY_VIEW_SCHEMA_REQUIRED';
 END IF;
 IF cardinality(NEW.parent_hashes)<>1 THEN RAISE EXCEPTION 'LINEAGE_VIEW_READ_REQUIRED'; END IF;
 SELECT * INTO p FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
   AND content_hash=NEW.parent_hashes[1] AND kind='READ';
 IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_VIEW_READ_REQUIRED'; END IF;
 raw := (p.document#>>'{body,sealed_text}')::jsonb;
 IF jsonb_typeof(b->'source_refs') IS DISTINCT FROM 'array' OR jsonb_array_length(b->'source_refs')=0 THEN
   RAISE EXCEPTION 'LINEAGE_VIEW_SOURCES_REQUIRED';
 END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(b->'source_refs') LOOP
   SELECT * INTO o FROM public.market_observation WHERE observation_id=(ref->>'observation_id')::bigint
     AND event_timestamp=(ref->>'event_timestamp_utc')::timestamptz;
   IF NOT FOUND OR o.timeframe<>'5m' OR o.data_type<>'OHLCV' OR NOT EXISTS(
      SELECT 1 FROM jsonb_array_elements(raw->'symbols') s,
      LATERAL jsonb_array_elements(s->'revisions') r
      WHERE (r->>'observation_id')::bigint=o.observation_id
        AND (r->>'event_timestamp_utc')::timestamptz=o.event_timestamp) THEN
     RAISE EXCEPTION 'LINEAGE_VIEW_SOURCE_NOT_IN_READ';
   END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_validate_authority_view BEFORE INSERT ON lineage_draft.artifact
FOR EACH ROW WHEN (NEW.kind='VIEW') EXECUTE FUNCTION lineage_draft.validate_authority_view();

CREATE FUNCTION lineage_draft.build_signal_authority() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; s jsonb; h text; v lineage_draft.artifact; expiry timestamptz; local_expiry timestamptz;
BEGIN
 b:=(NEW.document_text::jsonb)#>'{body}';
 IF b->>'authority_schema' IS DISTINCT FROM 'PRODUCER_AUTHORITY_V1'
    OR jsonb_typeof(b->'signals') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'LINEAGE_PRODUCER_DECLARATIONS_REQUIRED';
 END IF;
 IF EXISTS(SELECT 1 FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
     AND content_hash=ANY(NEW.parent_hashes) AND kind<>'VIEW') THEN
   RAISE EXCEPTION 'LINEAGE_PRODUCER_VIEW_REQUIRED';
 END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(b->'signals') LOOP
   IF coalesce(s->>'node_id','')='' OR coalesce(s->>'dataset_name','')=''
      OR s->>'row_hash' IS DISTINCT FROM lineage_draft.sha256_text(s->>'row_text')
      OR jsonb_typeof(s->'view_hashes') IS DISTINCT FROM 'array'
      OR jsonb_array_length(s->'view_hashes')=0 THEN
     RAISE EXCEPTION 'LINEAGE_PRODUCER_SIGNAL_INVALID';
   END IF;
   expiry:=NULL;
   FOR h IN SELECT jsonb_array_elements_text(s->'view_hashes') LOOP
     SELECT * INTO v FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
       AND content_hash=h AND h=ANY(NEW.parent_hashes) AND kind='VIEW';
     IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_SIGNAL_VIEW_MISSING'; END IF;
     SELECT min(x.last_start+interval '15 minutes') INTO local_expiry FROM (
       SELECT o.instrument_id,max(o.event_timestamp) last_start
       FROM jsonb_array_elements(v.document#>'{body,source_refs}') r
       JOIN public.market_observation o ON o.observation_id=(r->>'observation_id')::bigint
         AND o.event_timestamp=(r->>'event_timestamp_utc')::timestamptz
       GROUP BY o.instrument_id) x;
     IF local_expiry IS NULL THEN RAISE EXCEPTION 'LINEAGE_SOURCE_DEADLINE_MISSING'; END IF;
     expiry:=least(expiry,local_expiry);
   END LOOP;
   INSERT INTO lineage_draft.signal_authority VALUES(NEW.pipeline_run_id,NEW.content_hash,
     s->>'node_id',s->>'dataset_name',s->>'row_text',s->>'row_hash',expiry);
 END LOOP;
 RETURN NULL;
END $$;
CREATE TRIGGER build_signal_authority AFTER INSERT ON lineage_draft.artifact
FOR EACH ROW WHEN (NEW.kind='PRODUCER') EXECUTE FUNCTION lineage_draft.build_signal_authority();

CREATE FUNCTION lineage_draft.enforce_projection_authority() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; s jsonb; n integer; deadline timestamptz;
BEGIN
 b:=(NEW.document_text::jsonb)#>'{body}';
 IF EXISTS(SELECT 1 FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
     AND content_hash=ANY(NEW.parent_hashes) AND kind<>'PRODUCER') THEN
   RAISE EXCEPTION 'LINEAGE_PRODUCER_FRAGMENT_REQUIRED';
 END IF;
 IF EXISTS(SELECT 1 FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
     AND content_hash=ANY(NEW.parent_hashes) AND writer_xid=pg_current_xact_id()) THEN
   RAISE EXCEPTION 'LINEAGE_DIAGNOSTIC_COMMIT_REQUIRED';
 END IF;
 IF jsonb_typeof(b->'eligible_signals') IS DISTINCT FROM 'array' THEN
   RAISE EXCEPTION 'LINEAGE_ELIGIBLE_SIGNALS_REQUIRED';
 END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(b->'eligible_signals') LOOP
   SELECT count(*),min(source_expires_at) INTO n,deadline FROM lineage_draft.signal_authority
   WHERE pipeline_run_id=NEW.pipeline_run_id AND producer_hash=ANY(NEW.parent_hashes)
     AND signal_node_id=s->>'node_id' AND row_hash=s->>'row_hash';
   IF n<>1 THEN RAISE EXCEPTION 'LINEAGE_SIGNAL_NOT_DECLARED_BY_PRODUCER'; END IF;
   IF (b->>'authorized_until_utc')::timestamptz>deadline THEN
     RAISE EXCEPTION 'LINEAGE_SOURCE_EXPIRY_EXTENSION';
   END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_enforce_projection_authority BEFORE INSERT ON lineage_draft.artifact
FOR EACH ROW WHEN (NEW.kind='PROJECTION') EXECUTE FUNCTION lineage_draft.enforce_projection_authority();

CREATE FUNCTION lineage_draft.check_published_signal(sid bigint,rid uuid,phash text,nid text,rhash text)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE a lineage_draft.signal_authority; projection lineage_draft.artifact; matches integer;
BEGIN
 SELECT * INTO STRICT projection FROM lineage_draft.artifact WHERE pipeline_run_id=rid AND content_hash=phash;
 SELECT * INTO a FROM lineage_draft.signal_authority WHERE pipeline_run_id=rid
   AND producer_hash=ANY(projection.parent_hashes) AND signal_node_id=nid AND row_hash=rhash;
 IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_SIGNAL_NOT_DECLARED_BY_PRODUCER'; END IF;
 SELECT count(*) INTO matches FROM public.publication_dataset pd
 JOIN public.dataset_version dv ON dv.dataset_version_id=pd.dataset_version_id
 JOIN public.dataset_row dr ON dr.dataset_version_id=dv.dataset_version_id
 WHERE pd.publication_snapshot_id=sid AND pd.dataset_name=a.dataset_name
   AND dv.pipeline_run_id=rid AND dv.dataset_name=a.dataset_name AND dv.status='AVAILABLE'
   AND dr.payload=a.row_text::jsonb;
 IF matches<>1 THEN RAISE EXCEPTION 'LINEAGE_PUBLISHED_ROW_REQUIRED'; END IF;
END $$;

CREATE FUNCTION lineage_draft.final_physical_backing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage_draft.publication_evidence; p lineage_draft.artifact; s jsonb;
BEGIN
 SELECT * INTO STRICT e FROM lineage_draft.publication_evidence WHERE publication_snapshot_id=NEW.publication_snapshot_id;
 SELECT * INTO STRICT p FROM lineage_draft.artifact WHERE pipeline_run_id=e.pipeline_run_id AND content_hash=e.projection_hash;
 FOR s IN SELECT value FROM jsonb_array_elements(p.document#>'{body,eligible_signals}') LOOP
   PERFORM lineage_draft.check_published_signal(e.publication_snapshot_id,e.pipeline_run_id,e.projection_hash,s->>'node_id',s->>'row_hash');
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER final_physical_backing AFTER INSERT ON lineage_draft.publication_evidence
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage_draft.final_physical_backing();

CREATE FUNCTION lineage_draft.intent_physical_backing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM lineage_draft.check_published_signal(NEW.publication_snapshot_id,NEW.pipeline_run_id,
      NEW.projection_hash,NEW.signal_node_id,NEW.row_hash);
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER intent_physical_backing AFTER INSERT ON lineage_draft.alert_intent
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage_draft.intent_physical_backing();

CREATE FUNCTION lineage_draft.guard_delivery_lease() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state IN ('CLAIMED','SENDING') AND NEW.state<>'PENDING'
    AND (NEW.lease_owner IS DISTINCT FROM OLD.lease_owner OR NEW.lease_until IS DISTINCT FROM OLD.lease_until) THEN
   RAISE EXCEPTION 'OUTBOX_LEASE_IS_IMMUTABLE';
 END IF;
 IF OLD.state='SENDING' AND NEW.state IN ('DELIVERED','RETRYABLE','FAILED')
    AND OLD.lease_until<=clock_timestamp() THEN
   RAISE EXCEPTION 'OUTBOX_RESOLUTION_LEASE_EXPIRED';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_guard_delivery_lease BEFORE UPDATE ON lineage_draft.alert_delivery
FOR EACH ROW EXECUTE FUNCTION lineage_draft.guard_delivery_lease();
REVOKE ALL ON ALL TABLES IN SCHEMA lineage_draft FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage_draft FROM PUBLIC;
