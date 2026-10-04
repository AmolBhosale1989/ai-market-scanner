-- V0042: immutable lineage, source authority, publication and transactional outbox.
-- Requires 001-008. This validation-branch migration does not activate live workflows.
CREATE SCHEMA lineage;
REVOKE ALL ON SCHEMA lineage FROM PUBLIC;
CREATE FUNCTION lineage.sha256_text(value text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
 SELECT encode(sha256(convert_to(value,'UTF8')),'hex')
$$;
CREATE TABLE lineage.run_context (
 pipeline_run_id uuid PRIMARY KEY REFERENCES public.pipeline_run(pipeline_run_id),
 t0 timestamptz NOT NULL,
 snapshot_text text NOT NULL CHECK(snapshot_text=(snapshot_text::pg_snapshot)::text),
 source_commit text NOT NULL CHECK(source_commit ~ '^[0-9a-f]{40}$'),
 policy_version text NOT NULL CHECK(length(btrim(policy_version))>0),
 catalogue_hashes jsonb NOT NULL CHECK(jsonb_typeof(catalogue_hashes)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id()
);
CREATE FUNCTION lineage.validate_context() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r public.pipeline_run; hashes jsonb;
BEGIN
 SELECT * INTO STRICT r FROM public.pipeline_run WHERE pipeline_run_id=NEW.pipeline_run_id FOR SHARE;
 IF r.source_commit<>NEW.source_commit OR r.warehouse_as_of<>NEW.t0 OR r.status NOT IN ('STARTED','VALIDATING') OR NEW.t0>clock_timestamp() THEN
  RAISE EXCEPTION 'LINEAGE_RUN_CONTEXT_MISMATCH';
 END IF;
 SELECT jsonb_object_agg(dataset_name,content_hash) INTO hashes FROM public.frozen_catalogue
 WHERE pipeline_run_id=NEW.pipeline_run_id AND as_of_utc=NEW.t0 AND pg_snapshot=NEW.snapshot_text
 AND dataset_name IN ('master_universe','live_universe','tradable_universe');
 IF hashes IS NULL OR NOT(hashes ?& ARRAY['master_universe','live_universe','tradable_universe']) OR hashes<>NEW.catalogue_hashes THEN
  RAISE EXCEPTION 'LINEAGE_CATALOGUE_BINDING_MISMATCH';
 END IF;
 NEW.created_at:=clock_timestamp(); NEW.writer_xid:=pg_current_xact_id(); RETURN NEW;
END $$;
CREATE TRIGGER lineage_validate_context BEFORE INSERT ON lineage.run_context FOR EACH ROW EXECUTE FUNCTION lineage.validate_context();
CREATE FUNCTION lineage.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 RAISE EXCEPTION 'LINEAGE_APPEND_ONLY: % %',TG_TABLE_NAME,TG_OP;
END $$;
CREATE TRIGGER immutable_context BEFORE UPDATE OR DELETE ON lineage.run_context FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_context BEFORE TRUNCATE ON lineage.run_context FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
CREATE TABLE lineage.artifact (
 pipeline_run_id uuid NOT NULL REFERENCES lineage.run_context(pipeline_run_id),
 content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 kind text NOT NULL CHECK(kind IN ('READ','READ_FAILURE','VIEW','PRODUCER','PROJECTION')),
 document_text text NOT NULL CHECK(octet_length(document_text)<=67108864),
 document jsonb GENERATED ALWAYS AS (document_text::jsonb) STORED,
 parent_hashes text[] NOT NULL DEFAULT '{}', depth integer NOT NULL CHECK(depth>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 PRIMARY KEY(pipeline_run_id,content_hash), CHECK(content_hash=lineage.sha256_text(document_text)),
 CHECK(jsonb_typeof(document)='object'), CHECK(array_position(parent_hashes,NULL) IS NULL)
);
CREATE FUNCTION lineage.validate_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c lineage.run_context; d jsonb; b jsonb; parent_count integer; max_depth integer; inner_doc jsonb;
BEGIN
 SELECT * INTO STRICT c FROM lineage.run_context WHERE pipeline_run_id=NEW.pipeline_run_id;
 d:=NEW.document_text::jsonb; b:=d->'binding';
 IF b IS NULL OR (b->>'run_id')::uuid IS DISTINCT FROM c.pipeline_run_id
 OR (b->>'t0_utc')::timestamptz IS DISTINCT FROM c.t0 OR b->>'pg_snapshot' IS DISTINCT FROM c.snapshot_text
 OR b->>'source_commit' IS DISTINCT FROM c.source_commit OR b->>'policy_version' IS DISTINCT FROM c.policy_version
 OR d->>'kind' IS DISTINCT FROM NEW.kind OR d->'parent_hashes' IS DISTINCT FROM to_jsonb(NEW.parent_hashes) THEN
  RAISE EXCEPTION 'LINEAGE_ARTIFACT_BINDING_MISMATCH';
 END IF;
 IF cardinality(NEW.parent_hashes)<>(SELECT count(DISTINCT x) FROM unnest(NEW.parent_hashes) x) THEN RAISE EXCEPTION 'LINEAGE_DUPLICATE_PARENT'; END IF;
 SELECT count(*),max(depth) INTO parent_count,max_depth FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes);
 IF parent_count<>cardinality(NEW.parent_hashes) THEN RAISE EXCEPTION 'LINEAGE_PARENT_MISSING_OR_WRONG_RUN'; END IF;
 IF NEW.kind IN ('READ','READ_FAILURE') THEN
  IF parent_count<>0 THEN RAISE EXCEPTION 'LINEAGE_READ_HAS_PARENT'; END IF;
  IF d#>>'{body,sealed_text}' IS NULL OR d#>>'{body,sealed_hash}' IS DISTINCT FROM lineage.sha256_text(d#>>'{body,sealed_text}') THEN RAISE EXCEPTION 'LINEAGE_INNER_READ_HASH_MISMATCH'; END IF;
  inner_doc:=(d#>>'{body,sealed_text}')::jsonb;
  IF inner_doc->'binding' IS DISTINCT FROM b THEN RAISE EXCEPTION 'LINEAGE_INNER_READ_BINDING_MISMATCH'; END IF;
  IF NEW.kind='READ' AND inner_doc->>'outcome' IS DISTINCT FROM 'COMPLETE_RESULT' THEN RAISE EXCEPTION 'LINEAGE_FAILED_READ_IS_NOT_ABSENCE';
  ELSIF NEW.kind='READ_FAILURE' AND (inner_doc->>'outcome' IS DISTINCT FROM 'READ_OR_CAPTURE_FAILED' OR inner_doc ? 'symbols' OR inner_doc ? 'raw_result') THEN RAISE EXCEPTION 'LINEAGE_FAILED_READ_HAS_RESULT'; END IF;
 ELSE
  IF parent_count=0 THEN RAISE EXCEPTION 'LINEAGE_DERIVED_WITHOUT_PARENT'; END IF;
  IF EXISTS(SELECT 1 FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes) AND kind IN ('READ_FAILURE','PROJECTION')) THEN RAISE EXCEPTION 'LINEAGE_INVALID_PARENT_KIND'; END IF;
 END IF;
 NEW.depth:=coalesce(max_depth+1,0); NEW.created_at:=clock_timestamp(); NEW.writer_xid:=pg_current_xact_id(); RETURN NEW;
END $$;
CREATE TRIGGER validate_artifact BEFORE INSERT ON lineage.artifact FOR EACH ROW EXECUTE FUNCTION lineage.validate_artifact();
CREATE TRIGGER immutable_artifact BEFORE UPDATE OR DELETE ON lineage.artifact FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_artifact BEFORE TRUNCATE ON lineage.artifact FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
CREATE TABLE lineage.publication_evidence (
 publication_snapshot_id bigint PRIMARY KEY REFERENCES public.publication_snapshot(publication_snapshot_id),
 pipeline_run_id uuid NOT NULL REFERENCES lineage.run_context(pipeline_run_id), projection_hash text NOT NULL,
 checked_at timestamptz NOT NULL, authorized_until timestamptz NOT NULL CHECK(authorized_until>checked_at),
 writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 UNIQUE(publication_snapshot_id,pipeline_run_id,projection_hash,writer_xid),
 FOREIGN KEY(pipeline_run_id,projection_hash) REFERENCES lineage.artifact(pipeline_run_id,content_hash)
);
CREATE FUNCTION lineage.validate_publication_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p public.publication_snapshot; a lineage.artifact; c lineage.run_context;
BEGIN
 SELECT * INTO STRICT p FROM public.publication_snapshot WHERE publication_snapshot_id=NEW.publication_snapshot_id FOR UPDATE;
 SELECT * INTO STRICT a FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=NEW.projection_hash;
 SELECT * INTO STRICT c FROM lineage.run_context WHERE pipeline_run_id=NEW.pipeline_run_id;
 IF p.pipeline_run_id<>NEW.pipeline_run_id OR p.status<>'VALIDATING' OR a.kind<>'PROJECTION' OR a.writer_xid<>pg_current_xact_id()
 OR NEW.checked_at<c.t0 OR NEW.checked_at>clock_timestamp() OR NEW.authorized_until<=clock_timestamp()
 OR a.document#>>'{body,checked_at_utc}' IS NULL OR (a.document#>>'{body,checked_at_utc}')::timestamptz IS DISTINCT FROM NEW.checked_at
 OR a.document#>>'{body,authorized_until_utc}' IS NULL OR (a.document#>>'{body,authorized_until_utc}')::timestamptz IS DISTINCT FROM NEW.authorized_until THEN
  RAISE EXCEPTION 'LINEAGE_PUBLICATION_CERTIFICATE_INVALID';
 END IF;
 NEW.writer_xid:=pg_current_xact_id(); RETURN NEW;
END $$;
CREATE TRIGGER validate_publication_evidence BEFORE INSERT ON lineage.publication_evidence FOR EACH ROW EXECUTE FUNCTION lineage.validate_publication_evidence();
CREATE TRIGGER immutable_publication_evidence BEFORE UPDATE OR DELETE ON lineage.publication_evidence FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_publication_evidence BEFORE TRUNCATE ON lineage.publication_evidence FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
CREATE TABLE lineage.alert_intent (
 intent_id uuid PRIMARY KEY, publication_snapshot_id bigint NOT NULL, pipeline_run_id uuid NOT NULL, projection_hash text NOT NULL,
 publication_xid xid8 NOT NULL DEFAULT pg_current_xact_id(), channel text NOT NULL CHECK(channel='telegram'),
 recipient_key text NOT NULL CHECK(length(recipient_key)>0), semantic_key text NOT NULL CHECK(length(semantic_key)>0),
 signal_node_id text NOT NULL CHECK(length(signal_node_id)>0), row_hash text NOT NULL CHECK(row_hash ~ '^[0-9a-f]{64}$'),
 expires_at timestamptz NOT NULL, payload_text text NOT NULL CHECK(jsonb_typeof(payload_text::jsonb)='object'),
 payload_hash text NOT NULL CHECK(payload_hash=lineage.sha256_text(payload_text)),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(publication_snapshot_id,pipeline_run_id,projection_hash,publication_xid)
 REFERENCES lineage.publication_evidence(publication_snapshot_id,pipeline_run_id,projection_hash,writer_xid)
);
CREATE FUNCTION lineage.validate_intent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage.publication_evidence; a lineage.artifact; signal jsonb;
BEGIN
 SELECT * INTO STRICT e FROM lineage.publication_evidence WHERE publication_snapshot_id=NEW.publication_snapshot_id;
 IF e.writer_xid<>pg_current_xact_id() OR NEW.publication_xid<>pg_current_xact_id() OR NEW.expires_at>e.authorized_until OR NEW.expires_at<=clock_timestamp() THEN RAISE EXCEPTION 'OUTBOX_NOT_IN_PUBLICATION_TRANSACTION_OR_EXPIRED'; END IF;
 SELECT * INTO STRICT a FROM lineage.artifact WHERE pipeline_run_id=e.pipeline_run_id AND content_hash=e.projection_hash;
 SELECT s INTO signal FROM jsonb_array_elements(a.document#>'{body,eligible_signals}') s WHERE s->>'node_id'=NEW.signal_node_id AND s->>'row_hash'=NEW.row_hash;
 IF signal IS NULL THEN RAISE EXCEPTION 'OUTBOX_SIGNAL_NOT_ELIGIBLE'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER validate_intent BEFORE INSERT ON lineage.alert_intent FOR EACH ROW EXECUTE FUNCTION lineage.validate_intent();
CREATE TRIGGER immutable_intent BEFORE UPDATE OR DELETE ON lineage.alert_intent FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_intent BEFORE TRUNCATE ON lineage.alert_intent FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
CREATE TABLE lineage.alert_delivery (
 intent_id uuid PRIMARY KEY REFERENCES lineage.alert_intent(intent_id),
 state text NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','CLAIMED','SENDING','DELIVERED','RETRYABLE','DELIVERY_UNKNOWN','CANCELLED','FAILED')),
 not_before timestamptz NOT NULL DEFAULT clock_timestamp(), lease_owner uuid, lease_until timestamptz,
 fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0), attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 provider_receipt jsonb, reason text, updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX delivery_pending ON lineage.alert_delivery(not_before,intent_id) WHERE state IN ('PENDING','RETRYABLE');
CREATE FUNCTION lineage.seed_delivery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 INSERT INTO lineage.alert_delivery(intent_id) VALUES(NEW.intent_id); RETURN NEW;
END $$;
CREATE TRIGGER seed_delivery AFTER INSERT ON lineage.alert_intent FOR EACH ROW EXECUTE FUNCTION lineage.seed_delivery();
CREATE FUNCTION lineage.final_publication_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage.publication_evidence; p public.publication_snapshot;
BEGIN
 SELECT * INTO STRICT e FROM lineage.publication_evidence WHERE publication_snapshot_id=NEW.publication_snapshot_id;
 SELECT * INTO STRICT p FROM public.publication_snapshot WHERE publication_snapshot_id=e.publication_snapshot_id;
 IF p.status<>'PUBLISHED' OR p.published_at IS NULL OR p.pipeline_run_id<>e.pipeline_run_id OR clock_timestamp()>=e.authorized_until
 OR NOT EXISTS(SELECT 1 FROM public.publication_head WHERE mode=p.mode AND publication_snapshot_id=p.publication_snapshot_id) THEN RAISE EXCEPTION 'LINEAGE_PUBLICATION_NOT_FINAL_OR_EXPIRED'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER final_publication_check AFTER INSERT ON lineage.publication_evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage.final_publication_check();
CREATE FUNCTION lineage.require_certificate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; sid bigint;
BEGIN
 sid:=NEW.publication_snapshot_id;
 SELECT pipeline_run_id INTO STRICT rid FROM public.publication_snapshot WHERE publication_snapshot_id=sid;
 IF EXISTS(SELECT 1 FROM lineage.run_context WHERE pipeline_run_id=rid) AND NOT EXISTS(SELECT 1 FROM lineage.publication_evidence WHERE publication_snapshot_id=sid AND pipeline_run_id=rid) THEN RAISE EXCEPTION 'LINEAGE_PUBLICATION_CERTIFICATE_REQUIRED'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER require_lineage_certificate AFTER INSERT OR UPDATE ON public.publication_head DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage.require_certificate();
CREATE FUNCTION lineage.protect_bound_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF EXISTS(SELECT 1 FROM lineage.run_context WHERE pipeline_run_id=OLD.pipeline_run_id)
 AND (NEW.source_commit IS DISTINCT FROM OLD.source_commit OR NEW.warehouse_as_of IS DISTINCT FROM OLD.warehouse_as_of OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id) THEN RAISE EXCEPTION 'LINEAGE_BOUND_RUN_CANNOT_DRIFT'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_lineage_bound_run BEFORE UPDATE ON public.pipeline_run FOR EACH ROW EXECUTE FUNCTION lineage.protect_bound_run();
CREATE FUNCTION lineage.validate_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt jsonb; valid boolean:=false;
BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.state<>'PENDING' OR NEW.fence<>0 OR NEW.attempts<>0 OR NEW.lease_owner IS NOT NULL OR NEW.provider_receipt IS NOT NULL THEN RAISE EXCEPTION 'OUTBOX_INVALID_INITIAL_STATE'; END IF;
  RETURN NEW;
 END IF;
 IF NEW.intent_id<>OLD.intent_id THEN RAISE EXCEPTION 'OUTBOX_ID_IMMUTABLE'; END IF;
 valid:=(OLD.state IN ('PENDING','RETRYABLE') AND NEW.state IN ('CLAIMED','CANCELLED','FAILED'))
 OR (OLD.state='CLAIMED' AND NEW.state IN ('SENDING','CANCELLED','PENDING'))
 OR (OLD.state='SENDING' AND NEW.state IN ('DELIVERED','DELIVERY_UNKNOWN','RETRYABLE','FAILED'));
 IF NOT valid THEN RAISE EXCEPTION 'OUTBOX_ILLEGAL_STATE_TRANSITION'; END IF;
 IF NEW.state='CLAIMED' THEN
  IF NEW.fence<>OLD.fence+1 OR NEW.lease_owner IS NULL OR NEW.lease_until IS NULL OR NEW.lease_until<=clock_timestamp() OR NEW.attempts<>OLD.attempts OR OLD.not_before>clock_timestamp() THEN RAISE EXCEPTION 'OUTBOX_INVALID_CLAIM'; END IF;
 ELSIF NEW.fence<>OLD.fence THEN RAISE EXCEPTION 'OUTBOX_FENCE_MISMATCH'; END IF;
 IF NEW.state='PENDING' AND (OLD.lease_until IS NULL OR OLD.lease_until>clock_timestamp()) THEN RAISE EXCEPTION 'OUTBOX_CLAIM_NOT_EXPIRED'; END IF;
 IF NEW.state='SENDING' THEN
  IF NEW.attempts<>OLD.attempts+1 OR OLD.lease_until<=clock_timestamp() OR NEW.lease_owner IS DISTINCT FROM OLD.lease_owner THEN RAISE EXCEPTION 'OUTBOX_SEND_WITHOUT_VALID_LEASE'; END IF;
  IF NOT EXISTS(SELECT 1 FROM lineage.alert_intent i JOIN lineage.publication_evidence e USING(publication_snapshot_id)
    JOIN public.publication_snapshot p USING(publication_snapshot_id)
    JOIN public.publication_head h ON h.mode=p.mode AND h.publication_snapshot_id=p.publication_snapshot_id
    WHERE i.intent_id=NEW.intent_id AND p.status='PUBLISHED' AND i.expires_at>clock_timestamp() AND e.authorized_until>clock_timestamp()) THEN RAISE EXCEPTION 'OUTBOX_SEND_PUBLICATION_INVALID_OR_EXPIRED'; END IF;
 ELSIF NEW.attempts<>OLD.attempts THEN RAISE EXCEPTION 'OUTBOX_ATTEMPT_COUNT_INVALID'; END IF;
 receipt:=NEW.provider_receipt;
 IF NEW.state='DELIVERED' AND (receipt IS NULL OR receipt->>'http_status' IS DISTINCT FROM '200'
 OR receipt#>'{body,ok}' IS DISTINCT FROM 'true'::jsonb OR jsonb_typeof(receipt#>'{body,result,message_id}') IS DISTINCT FROM 'number'
 OR NOT coalesce((receipt#>>'{body,result,message_id}') ~ '^[1-9][0-9]*$',false)) THEN RAISE EXCEPTION 'OUTBOX_APPLICATION_ACK_REQUIRED'; END IF;
 IF NEW.state='RETRYABLE' THEN
  IF receipt IS NULL OR receipt#>'{body,ok}' IS DISTINCT FROM 'false'::jsonb OR receipt#>>'{body,error_code}' IS DISTINCT FROM '429' OR NOT coalesce((receipt#>>'{body,parameters,retry_after}') ~ '^[1-9][0-9]*$',false) THEN RAISE EXCEPTION 'OUTBOX_CONFIRMED_REJECTION_REQUIRED'; END IF;
  IF (receipt#>>'{body,parameters,retry_after}')::numeric>86400 THEN RAISE EXCEPTION 'OUTBOX_RETRY_INTERVAL_UNSUPPORTED'; END IF;
  NEW.not_before:=greatest(NEW.not_before,clock_timestamp()+make_interval(secs=>(receipt#>>'{body,parameters,retry_after}')::integer));
 END IF;
 NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER validate_delivery BEFORE INSERT OR UPDATE ON lineage.alert_delivery FOR EACH ROW EXECUTE FUNCTION lineage.validate_delivery();
CREATE TRIGGER no_delete_delivery BEFORE DELETE ON lineage.alert_delivery FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_delivery BEFORE TRUNCATE ON lineage.alert_delivery FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
-- Re-evaluate the full selected composite revision inventory, never arbitrary saved SQL.
CREATE FUNCTION lineage.validate_raw_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d jsonb; q jsonb; c lineage.run_context; wanted text[]; observed timestamptz; lo timestamptz; hi timestamptz; bad boolean; expected_catalogue bigint[]; declared_catalogue bigint[];
BEGIN
 d:=((NEW.document_text::jsonb)#>>'{body,sealed_text}')::jsonb; q:=d->'query';
 SELECT * INTO STRICT c FROM lineage.run_context WHERE pipeline_run_id=NEW.pipeline_run_id;
 SELECT array_agg(DISTINCT value ORDER BY value) INTO wanted FROM jsonb_array_elements_text(q->'requested_tickers');
 observed:=(q->>'as_of_utc')::timestamptz; lo:=(q->>'event_lower_utc')::timestamptz; hi:=(q->>'event_upper_utc')::timestamptz;
 IF wanted IS NULL OR cardinality(wanted)=0 OR observed IS NULL OR hi IS NULL OR observed>c.t0 OR hi>observed OR lo>hi OR q->>'data_type' IS DISTINCT FROM 'OHLCV'
 OR NOT coalesce(q->>'timeframe' IN ('1d','5m'),false) OR (q->>'timeframe'='5m' AND hi+interval '5 minutes'>observed) THEN RAISE EXCEPTION 'LINEAGE_RAW_SCOPE_INVALID'; END IF;
 WITH v AS (SELECT DISTINCT ON (instrument_id) * FROM public.instrument_catalogue_revision
 WHERE known_at<=observed AND pg_visible_in_snapshot(writer_xid,c.snapshot_text::pg_snapshot) ORDER BY instrument_id,revision_id DESC)
 SELECT coalesce(array_agg(revision_id ORDER BY revision_id),'{}'::bigint[]) INTO expected_catalogue FROM v WHERE NOT deleted AND canonical_symbol=ANY(wanted);
 SELECT coalesce(array_agg((r->>'catalogue_revision_id')::bigint ORDER BY (r->>'catalogue_revision_id')::bigint),'{}'::bigint[]) INTO declared_catalogue
 FROM jsonb_array_elements(d->'symbols') s CROSS JOIN LATERAL jsonb_array_elements(s->'catalogue') r;
 IF expected_catalogue<>declared_catalogue THEN RAISE EXCEPTION 'LINEAGE_CATALOGUE_REVISIONS_MISMATCH'; END IF;
 SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(d->'symbols') s CROSS JOIN LATERAL jsonb_array_elements(s->'catalogue') r
 JOIN public.instrument_catalogue_revision v ON v.revision_id=(r->>'catalogue_revision_id')::bigint
 WHERE v.instrument_id IS DISTINCT FROM (r->>'instrument_id')::bigint OR v.canonical_symbol IS DISTINCT FROM s->>'symbol'
 OR v.writer_xid IS DISTINCT FROM (r->>'writer_xid')::xid8 OR v.known_at IS DISTINCT FROM (r->>'known_at_utc')::timestamptz) INTO bad;
 IF bad THEN RAISE EXCEPTION 'LINEAGE_CATALOGUE_METADATA_MISMATCH'; END IF;
 WITH v AS (SELECT DISTINCT ON (instrument_id) * FROM public.instrument_catalogue_revision
 WHERE known_at<=observed AND pg_visible_in_snapshot(writer_xid,c.snapshot_text::pg_snapshot) ORDER BY instrument_id,revision_id DESC),
 ranked AS (SELECT o.observation_id,o.event_timestamp,row_number() OVER(PARTITION BY o.instrument_id,o.data_type,o.timeframe,o.event_timestamp ORDER BY o.ingested_at DESC,o.observation_id DESC) AS rn
 FROM public.market_observation o JOIN v ON v.instrument_id=o.instrument_id
 WHERE NOT v.deleted AND v.canonical_symbol=ANY(wanted) AND o.data_type='OHLCV' AND o.timeframe=q->>'timeframe'
 AND o.event_timestamp<=hi AND (lo IS NULL OR o.event_timestamp>=lo) AND o.ingested_at<=observed AND pg_visible_in_snapshot(o.writer_xid,c.snapshot_text::pg_snapshot)),
 expected AS (SELECT observation_id,event_timestamp FROM ranked WHERE rn=1),
 declared AS (SELECT (r->>'observation_id')::bigint AS observation_id,(r->>'event_timestamp_utc')::timestamptz AS event_timestamp
 FROM jsonb_array_elements(d->'symbols') s CROSS JOIN LATERAL jsonb_array_elements(s->'revisions') r)
 SELECT EXISTS((SELECT * FROM expected EXCEPT ALL SELECT * FROM declared) UNION ALL (SELECT * FROM declared EXCEPT ALL SELECT * FROM expected)) INTO bad;
 IF bad THEN RAISE EXCEPTION 'LINEAGE_SOURCE_INVENTORY_MISMATCH'; END IF;
 SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(d->'symbols') s CROSS JOIN LATERAL jsonb_array_elements(s->'revisions') r
 LEFT JOIN public.market_observation o ON o.observation_id=(r->>'observation_id')::bigint AND o.event_timestamp=(r->>'event_timestamp_utc')::timestamptz
 WHERE o.observation_id IS NULL OR o.instrument_id IS DISTINCT FROM (r->>'instrument_id')::bigint
 OR o.writer_xid IS DISTINCT FROM (r->>'writer_xid')::xid8 OR o.ingested_at IS DISTINCT FROM (r->>'ingested_at_utc')::timestamptz
 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'catalogue') cat WHERE (cat->>'instrument_id')::bigint=o.instrument_id)) INTO bad;
 IF bad THEN RAISE EXCEPTION 'LINEAGE_SOURCE_REVISION_METADATA_MISMATCH'; END IF;
 SELECT EXISTS(SELECT 1 FROM jsonb_array_elements(d->'symbols') s WHERE (s->>'state') IS DISTINCT FROM
 CASE WHEN jsonb_array_length(s->'revisions')>0 THEN 'OBSERVATIONS_RETURNED' WHEN jsonb_array_length(s->'catalogue')>0 THEN 'NO_OBSERVATIONS_IN_SCOPED_RESULT' ELSE 'NO_VISIBLE_CATALOGUE_MATCH' END
 OR (jsonb_array_length(s->'revisions')=0 AND s#>>'{absence_certificate,scope_query_hash}' IS DISTINCT FROM d->>'query_hash')) INTO bad;
 IF bad THEN RAISE EXCEPTION 'LINEAGE_ABSENCE_INVENTORY_INVALID'; END IF;
 IF wanted IS DISTINCT FROM (SELECT array_agg(s->>'symbol' ORDER BY s->>'symbol') FROM jsonb_array_elements(d->'symbols') s) THEN RAISE EXCEPTION 'LINEAGE_SYMBOL_INVENTORY_INVALID'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER validate_raw_manifest BEFORE INSERT ON lineage.artifact FOR EACH ROW WHEN (NEW.kind='READ') EXECUTE FUNCTION lineage.validate_raw_manifest();
CREATE TABLE lineage.signal_authority (
 pipeline_run_id uuid NOT NULL,producer_hash text NOT NULL,signal_node_id text NOT NULL,dataset_name text NOT NULL,
 row_text text NOT NULL CHECK(jsonb_typeof(row_text::jsonb)='object'), row_hash text NOT NULL CHECK(row_hash=lineage.sha256_text(row_text)),
 source_expires_at timestamptz NOT NULL,PRIMARY KEY(pipeline_run_id,producer_hash,signal_node_id),
 FOREIGN KEY(pipeline_run_id,producer_hash) REFERENCES lineage.artifact(pipeline_run_id,content_hash)
);
CREATE TRIGGER immutable_signal_authority BEFORE UPDATE OR DELETE ON lineage.signal_authority FOR EACH ROW EXECUTE FUNCTION lineage.reject_mutation();
CREATE TRIGGER no_truncate_signal_authority BEFORE TRUNCATE ON lineage.signal_authority FOR EACH STATEMENT EXECUTE FUNCTION lineage.reject_mutation();
CREATE FUNCTION lineage.validate_authority_view() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; ref jsonb; p lineage.artifact; raw jsonb; o public.market_observation;
BEGIN
 b:=(NEW.document_text::jsonb)#>'{body}';
 IF b->>'authority_schema' IS DISTINCT FROM 'LIVE_VIEW_V1' THEN RAISE EXCEPTION 'LINEAGE_AUTHORITY_VIEW_SCHEMA_REQUIRED'; END IF;
 IF cardinality(NEW.parent_hashes)<>1 THEN RAISE EXCEPTION 'LINEAGE_VIEW_READ_REQUIRED'; END IF;
 SELECT * INTO p FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=NEW.parent_hashes[1] AND kind='READ';
 IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_VIEW_READ_REQUIRED'; END IF;
 raw:=(p.document#>>'{body,sealed_text}')::jsonb;
 IF jsonb_typeof(b->'source_refs') IS DISTINCT FROM 'array' OR jsonb_array_length(b->'source_refs')=0 THEN RAISE EXCEPTION 'LINEAGE_VIEW_SOURCES_REQUIRED'; END IF;
 FOR ref IN SELECT value FROM jsonb_array_elements(b->'source_refs') LOOP
  SELECT * INTO o FROM public.market_observation WHERE observation_id=(ref->>'observation_id')::bigint AND event_timestamp=(ref->>'event_timestamp_utc')::timestamptz;
  IF NOT FOUND OR o.timeframe<>'5m' OR o.data_type<>'OHLCV' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(raw->'symbols') s,LATERAL jsonb_array_elements(s->'revisions') r
   WHERE (r->>'observation_id')::bigint=o.observation_id AND (r->>'event_timestamp_utc')::timestamptz=o.event_timestamp) THEN RAISE EXCEPTION 'LINEAGE_VIEW_SOURCE_NOT_IN_READ'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_validate_authority_view BEFORE INSERT ON lineage.artifact FOR EACH ROW WHEN (NEW.kind='VIEW') EXECUTE FUNCTION lineage.validate_authority_view();
CREATE FUNCTION lineage.build_signal_authority() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; s jsonb; h text; v lineage.artifact; expiry timestamptz; local_expiry timestamptz;
BEGIN
 b:=(NEW.document_text::jsonb)#>'{body}';
 IF b->>'authority_schema' IS DISTINCT FROM 'PRODUCER_AUTHORITY_V1' OR jsonb_typeof(b->'signals') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_DECLARATIONS_REQUIRED'; END IF;
 IF EXISTS(SELECT 1 FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes) AND kind NOT IN ('VIEW','PRODUCER')) THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_VIEW_REQUIRED'; END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(b->'signals') LOOP
  IF coalesce(s->>'node_id','')='' OR coalesce(s->>'dataset_name','')='' OR s->>'row_hash' IS DISTINCT FROM lineage.sha256_text(s->>'row_text')
   OR jsonb_typeof(s->'view_hashes') IS DISTINCT FROM 'array' OR jsonb_array_length(s->'view_hashes')=0 THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_SIGNAL_INVALID'; END IF;
  expiry:=NULL;
  FOR h IN SELECT jsonb_array_elements_text(s->'view_hashes') LOOP
   SELECT * INTO v FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=h AND h=ANY(NEW.parent_hashes) AND kind='VIEW';
   IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_SIGNAL_VIEW_MISSING'; END IF;
   SELECT min(x.last_start+interval '15 minutes') INTO local_expiry FROM (
    SELECT o.instrument_id,max(o.event_timestamp) last_start FROM jsonb_array_elements(v.document#>'{body,source_refs}') r
    JOIN public.market_observation o ON o.observation_id=(r->>'observation_id')::bigint AND o.event_timestamp=(r->>'event_timestamp_utc')::timestamptz GROUP BY o.instrument_id) x;
   IF local_expiry IS NULL THEN RAISE EXCEPTION 'LINEAGE_SOURCE_DEADLINE_MISSING'; END IF;
   expiry:=least(expiry,local_expiry);
  END LOOP;
  INSERT INTO lineage.signal_authority VALUES(NEW.pipeline_run_id,NEW.content_hash,s->>'node_id',s->>'dataset_name',s->>'row_text',s->>'row_hash',expiry);
 END LOOP;
 RETURN NULL;
END $$;
CREATE TRIGGER build_signal_authority AFTER INSERT ON lineage.artifact FOR EACH ROW WHEN (NEW.kind='PRODUCER') EXECUTE FUNCTION lineage.build_signal_authority();
CREATE FUNCTION lineage.enforce_projection_authority() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE b jsonb; s jsonb; n integer; deadline timestamptz;
BEGIN
 b:=(NEW.document_text::jsonb)#>'{body}';
 IF EXISTS(SELECT 1 FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes) AND kind<>'PRODUCER') THEN RAISE EXCEPTION 'LINEAGE_PRODUCER_FRAGMENT_REQUIRED'; END IF;
 IF EXISTS(SELECT 1 FROM lineage.artifact WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes) AND writer_xid=pg_current_xact_id()) THEN RAISE EXCEPTION 'LINEAGE_DIAGNOSTIC_COMMIT_REQUIRED'; END IF;
 IF jsonb_typeof(b->'eligible_signals') IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'LINEAGE_ELIGIBLE_SIGNALS_REQUIRED'; END IF;
 FOR s IN SELECT value FROM jsonb_array_elements(b->'eligible_signals') LOOP
  SELECT count(*),min(source_expires_at) INTO n,deadline FROM lineage.signal_authority
  WHERE pipeline_run_id=NEW.pipeline_run_id AND producer_hash=ANY(NEW.parent_hashes) AND signal_node_id=s->>'node_id' AND row_hash=s->>'row_hash';
  IF n<>1 THEN RAISE EXCEPTION 'LINEAGE_SIGNAL_NOT_DECLARED_BY_PRODUCER'; END IF;
  IF (b->>'authorized_until_utc')::timestamptz>deadline THEN RAISE EXCEPTION 'LINEAGE_SOURCE_EXPIRY_EXTENSION'; END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_enforce_projection_authority BEFORE INSERT ON lineage.artifact FOR EACH ROW WHEN (NEW.kind='PROJECTION') EXECUTE FUNCTION lineage.enforce_projection_authority();
CREATE FUNCTION lineage.check_published_signal(sid bigint,rid uuid,phash text,nid text,rhash text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE a lineage.signal_authority; projection lineage.artifact; matches integer;
BEGIN
 SELECT * INTO STRICT projection FROM lineage.artifact WHERE pipeline_run_id=rid AND content_hash=phash;
 SELECT * INTO a FROM lineage.signal_authority WHERE pipeline_run_id=rid AND producer_hash=ANY(projection.parent_hashes) AND signal_node_id=nid AND row_hash=rhash;
 IF NOT FOUND THEN RAISE EXCEPTION 'LINEAGE_SIGNAL_NOT_DECLARED_BY_PRODUCER'; END IF;
 SELECT count(*) INTO matches FROM public.publication_dataset pd JOIN public.dataset_version dv ON dv.dataset_version_id=pd.dataset_version_id
 JOIN public.dataset_row dr ON dr.dataset_version_id=dv.dataset_version_id WHERE pd.publication_snapshot_id=sid AND pd.dataset_name=a.dataset_name
 AND dv.pipeline_run_id=rid AND dv.dataset_name=a.dataset_name AND dv.status='AVAILABLE' AND dr.payload=a.row_text::jsonb;
 IF matches<>1 THEN RAISE EXCEPTION 'LINEAGE_PUBLISHED_ROW_REQUIRED'; END IF;
END $$;
CREATE FUNCTION lineage.final_physical_backing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage.publication_evidence; p lineage.artifact; s jsonb;
BEGIN
 SELECT * INTO STRICT e FROM lineage.publication_evidence WHERE publication_snapshot_id=NEW.publication_snapshot_id;
 SELECT * INTO STRICT p FROM lineage.artifact WHERE pipeline_run_id=e.pipeline_run_id AND content_hash=e.projection_hash;
 FOR s IN SELECT value FROM jsonb_array_elements(p.document#>'{body,eligible_signals}') LOOP
  PERFORM lineage.check_published_signal(e.publication_snapshot_id,e.pipeline_run_id,e.projection_hash,s->>'node_id',s->>'row_hash');
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER final_physical_backing AFTER INSERT ON lineage.publication_evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage.final_physical_backing();
CREATE FUNCTION lineage.intent_physical_backing() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 PERFORM lineage.check_published_signal(NEW.publication_snapshot_id,NEW.pipeline_run_id,NEW.projection_hash,NEW.signal_node_id,NEW.row_hash); RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER intent_physical_backing AFTER INSERT ON lineage.alert_intent DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage.intent_physical_backing();
CREATE FUNCTION lineage.guard_delivery_lease() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF OLD.state IN ('CLAIMED','SENDING') AND NEW.state<>'PENDING' AND (NEW.lease_owner IS DISTINCT FROM OLD.lease_owner OR NEW.lease_until IS DISTINCT FROM OLD.lease_until) THEN RAISE EXCEPTION 'OUTBOX_LEASE_IS_IMMUTABLE'; END IF;
 IF OLD.state='SENDING' AND NEW.state IN ('DELIVERED','RETRYABLE','FAILED') AND OLD.lease_until<=clock_timestamp() THEN RAISE EXCEPTION 'OUTBOX_RESOLUTION_LEASE_EXPIRED'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER zz_guard_delivery_lease BEFORE UPDATE ON lineage.alert_delivery FOR EACH ROW EXECUTE FUNCTION lineage.guard_delivery_lease();
-- A unique reservation and row lock serialize same-recipient, same-key publishers.
CREATE TABLE lineage.alert_reservation (
 channel text NOT NULL,recipient_key text NOT NULL,semantic_key text NOT NULL,
 last_intent_id uuid REFERENCES lineage.alert_intent(intent_id) DEFERRABLE INITIALLY DEFERRED,
 cooldown_until timestamptz NOT NULL DEFAULT '-infinity',PRIMARY KEY(channel,recipient_key,semantic_key)
);
CREATE INDEX alert_intent_semantic_history ON lineage.alert_intent(channel,recipient_key,semantic_key,created_at DESC);
CREATE FUNCTION lineage.reserve_intent() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,lineage,public,pg_temp AS $$
DECLARE old_id uuid; until_at timestamptz; delivery text;
BEGIN
 INSERT INTO lineage.alert_reservation(channel,recipient_key,semantic_key) VALUES(NEW.channel,NEW.recipient_key,NEW.semantic_key) ON CONFLICT(channel,recipient_key,semantic_key) DO NOTHING;
 SELECT last_intent_id,cooldown_until INTO STRICT old_id,until_at FROM lineage.alert_reservation
 WHERE channel=NEW.channel AND recipient_key=NEW.recipient_key AND semantic_key=NEW.semantic_key FOR UPDATE;
 IF old_id IS NOT NULL AND until_at>clock_timestamp() THEN
  SELECT state INTO STRICT delivery FROM lineage.alert_delivery WHERE intent_id=old_id;
  IF delivery<>'CANCELLED' THEN RETURN NULL; END IF;
 END IF;
 NEW.created_at:=clock_timestamp();
 UPDATE lineage.alert_reservation SET last_intent_id=NEW.intent_id,cooldown_until=clock_timestamp()+interval '12 hours'
 WHERE channel=NEW.channel AND recipient_key=NEW.recipient_key AND semantic_key=NEW.semantic_key;
 RETURN NEW;
END $$;
CREATE TRIGGER zzzz_reserve_intent BEFORE INSERT ON lineage.alert_intent FOR EACH ROW EXECUTE FUNCTION lineage.reserve_intent();
-- Only the migration owner materializes authority and delivery rows inside triggers.
-- Runtime roles cannot call trigger functions directly or change their search path.
DO $harden$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure signature FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='lineage' AND p.prorettype='trigger'::regtype LOOP
  EXECUTE format('ALTER FUNCTION %s SECURITY DEFINER',f.signature);
  EXECUTE format('ALTER FUNCTION %s SET search_path=pg_catalog,lineage,public,pg_temp',f.signature);
 END LOOP;
END $harden$;
ALTER FUNCTION lineage.check_published_signal(bigint,uuid,text,text,text) SECURITY DEFINER;
ALTER FUNCTION lineage.check_published_signal(bigint,uuid,text,text,text) SET search_path=pg_catalog,lineage,public,pg_temp;
REVOKE ALL ON ALL TABLES IN SCHEMA lineage FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage FROM PUBLIC;
