-- REVIEW DRAFT. Not registered as a production migration.
-- Requires the frozen repository's public schema through migration 008.
-- Use only in a newly created isolated database until integration CI passes.
-- SHA256 here hashes original UTF-8 bytes, NOT PostgreSQL's JSONB rendering.
CREATE SCHEMA lineage_draft;
REVOKE ALL ON SCHEMA lineage_draft FROM PUBLIC;

CREATE FUNCTION lineage_draft.sha256_text(value text) RETURNS text
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
  SELECT encode(sha256(convert_to(value,'UTF8')), 'hex')
$$;

CREATE TABLE lineage_draft.run_context (
  pipeline_run_id uuid PRIMARY KEY REFERENCES public.pipeline_run(pipeline_run_id),
  t0 timestamptz NOT NULL,
  snapshot_text text NOT NULL CHECK (snapshot_text = (snapshot_text::pg_snapshot)::text),
  source_commit text NOT NULL CHECK (source_commit ~ '^[0-9a-f]{40}$'),
  policy_version text NOT NULL CHECK (length(btrim(policy_version)) > 0),
  catalogue_hashes jsonb NOT NULL CHECK (jsonb_typeof(catalogue_hashes)='object'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id()
);

CREATE FUNCTION lineage_draft.validate_context() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE r public.pipeline_run; hashes jsonb;
BEGIN
  SELECT * INTO STRICT r FROM public.pipeline_run
    WHERE pipeline_run_id=NEW.pipeline_run_id FOR SHARE;
  IF r.source_commit<>NEW.source_commit OR r.warehouse_as_of<>NEW.t0
     OR r.status NOT IN ('STARTED','VALIDATING') OR NEW.t0>clock_timestamp() THEN
    RAISE EXCEPTION 'LINEAGE_RUN_CONTEXT_MISMATCH';
  END IF;
  SELECT jsonb_object_agg(dataset_name,content_hash) INTO hashes
    FROM public.frozen_catalogue WHERE pipeline_run_id=NEW.pipeline_run_id
      AND as_of_utc=NEW.t0 AND pg_snapshot=NEW.snapshot_text
      AND dataset_name IN ('master_universe','live_universe','tradable_universe');
  IF hashes IS NULL OR NOT (hashes ?& ARRAY['master_universe','live_universe','tradable_universe'])
     OR hashes<>NEW.catalogue_hashes THEN
    RAISE EXCEPTION 'LINEAGE_CATALOGUE_BINDING_MISMATCH';
  END IF;
  NEW.created_at := clock_timestamp();
  NEW.writer_xid := pg_current_xact_id();
  RETURN NEW;
END $$;
CREATE TRIGGER lineage_validate_context BEFORE INSERT ON lineage_draft.run_context
FOR EACH ROW EXECUTE FUNCTION lineage_draft.validate_context();

CREATE FUNCTION lineage_draft.reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN
  RAISE EXCEPTION 'LINEAGE_APPEND_ONLY: % %', TG_TABLE_NAME, TG_OP;
END $$;
CREATE TRIGGER immutable_context BEFORE UPDATE OR DELETE ON lineage_draft.run_context
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_context BEFORE TRUNCATE ON lineage_draft.run_context
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();

-- Derived evidence is intentionally written after T0. Never require its
-- writer_xid to be visible inside the raw market snapshot.
CREATE TABLE lineage_draft.artifact (
  pipeline_run_id uuid NOT NULL REFERENCES lineage_draft.run_context(pipeline_run_id),
  content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  kind text NOT NULL CHECK (kind IN ('READ','READ_FAILURE','VIEW','PRODUCER','PROJECTION')),
  document_text text NOT NULL CHECK (octet_length(document_text)<=67108864),
  document jsonb GENERATED ALWAYS AS (document_text::jsonb) STORED,
  parent_hashes text[] NOT NULL DEFAULT '{}',
  depth integer NOT NULL CHECK (depth>=0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
  PRIMARY KEY(pipeline_run_id,content_hash),
  CHECK(content_hash=lineage_draft.sha256_text(document_text)),
  CHECK(jsonb_typeof(document)='object'),
  CHECK(array_position(parent_hashes,NULL) IS NULL)
);

CREATE FUNCTION lineage_draft.validate_artifact() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE c lineage_draft.run_context; d jsonb; b jsonb; parent_count integer; max_depth integer;
        inner_doc jsonb;
BEGIN
  SELECT * INTO STRICT c FROM lineage_draft.run_context
    WHERE pipeline_run_id=NEW.pipeline_run_id;
  d:=NEW.document_text::jsonb; b:=d->'binding';
  IF b IS NULL OR (b->>'run_id')::uuid IS DISTINCT FROM c.pipeline_run_id
    OR (b->>'t0_utc')::timestamptz IS DISTINCT FROM c.t0
    OR b->>'pg_snapshot' IS DISTINCT FROM c.snapshot_text
    OR b->>'source_commit' IS DISTINCT FROM c.source_commit
    OR b->>'policy_version' IS DISTINCT FROM c.policy_version
    OR d->>'kind' IS DISTINCT FROM NEW.kind
    OR d->'parent_hashes' IS DISTINCT FROM to_jsonb(NEW.parent_hashes) THEN
    RAISE EXCEPTION 'LINEAGE_ARTIFACT_BINDING_MISMATCH';
  END IF;
  IF cardinality(NEW.parent_hashes) <> (SELECT count(DISTINCT x) FROM unnest(NEW.parent_hashes) x) THEN
    RAISE EXCEPTION 'LINEAGE_DUPLICATE_PARENT';
  END IF;
  SELECT count(*),max(depth) INTO parent_count,max_depth FROM lineage_draft.artifact
    WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=ANY(NEW.parent_hashes);
  IF parent_count<>cardinality(NEW.parent_hashes) THEN
    RAISE EXCEPTION 'LINEAGE_PARENT_MISSING_OR_WRONG_RUN';
  END IF;
  IF NEW.kind IN ('READ','READ_FAILURE') THEN
    IF parent_count<>0 THEN RAISE EXCEPTION 'LINEAGE_READ_HAS_PARENT'; END IF;
    IF d#>>'{body,sealed_text}' IS NULL OR d#>>'{body,sealed_hash}' IS DISTINCT FROM
       lineage_draft.sha256_text(d#>>'{body,sealed_text}') THEN
      RAISE EXCEPTION 'LINEAGE_INNER_READ_HASH_MISMATCH';
    END IF;
    inner_doc:=(d#>>'{body,sealed_text}')::jsonb;
    IF inner_doc->'binding' IS DISTINCT FROM b THEN
      RAISE EXCEPTION 'LINEAGE_INNER_READ_BINDING_MISMATCH';
    END IF;
    IF NEW.kind='READ' AND inner_doc->>'outcome' IS DISTINCT FROM 'COMPLETE_RESULT' THEN
      RAISE EXCEPTION 'LINEAGE_FAILED_READ_IS_NOT_ABSENCE';
    ELSIF NEW.kind='READ_FAILURE' AND (inner_doc->>'outcome' IS DISTINCT FROM 'READ_OR_CAPTURE_FAILED'
          OR inner_doc ? 'symbols' OR inner_doc ? 'raw_result') THEN
      RAISE EXCEPTION 'LINEAGE_FAILED_READ_HAS_RESULT';
    END IF;
  ELSE
    IF parent_count=0 THEN RAISE EXCEPTION 'LINEAGE_DERIVED_WITHOUT_PARENT'; END IF;
    IF EXISTS(SELECT 1 FROM lineage_draft.artifact WHERE pipeline_run_id=NEW.pipeline_run_id
        AND content_hash=ANY(NEW.parent_hashes) AND kind IN ('READ_FAILURE','PROJECTION')) THEN
      RAISE EXCEPTION 'LINEAGE_INVALID_PARENT_KIND';
    END IF;
  END IF;
  -- Parents already exist and are immutable, so insertion cannot form a cycle.
  NEW.depth:=coalesce(max_depth+1,0);
  NEW.created_at:=clock_timestamp(); NEW.writer_xid:=pg_current_xact_id();
  RETURN NEW;
END $$;
CREATE TRIGGER validate_artifact BEFORE INSERT ON lineage_draft.artifact
FOR EACH ROW EXECUTE FUNCTION lineage_draft.validate_artifact();
CREATE TRIGGER immutable_artifact BEFORE UPDATE OR DELETE ON lineage_draft.artifact
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_artifact BEFORE TRUNCATE ON lineage_draft.artifact
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();

-- One fresh verdict/projection certificate belongs to one publication attempt.
-- The canonical producer graph remains in artifact; no evidence is deleted.
CREATE TABLE lineage_draft.publication_evidence (
  publication_snapshot_id bigint PRIMARY KEY REFERENCES public.publication_snapshot(publication_snapshot_id),
  pipeline_run_id uuid NOT NULL REFERENCES lineage_draft.run_context(pipeline_run_id),
  projection_hash text NOT NULL,
  checked_at timestamptz NOT NULL,
  authorized_until timestamptz NOT NULL CHECK (authorized_until>checked_at),
  writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
  UNIQUE(publication_snapshot_id,pipeline_run_id,projection_hash,writer_xid),
  FOREIGN KEY(pipeline_run_id,projection_hash) REFERENCES lineage_draft.artifact(pipeline_run_id,content_hash)
);
CREATE FUNCTION lineage_draft.validate_publication_evidence() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE p public.publication_snapshot; a lineage_draft.artifact; c lineage_draft.run_context;
BEGIN
  SELECT * INTO STRICT p FROM public.publication_snapshot
    WHERE publication_snapshot_id=NEW.publication_snapshot_id FOR UPDATE;
  SELECT * INTO STRICT a FROM lineage_draft.artifact
    WHERE pipeline_run_id=NEW.pipeline_run_id AND content_hash=NEW.projection_hash;
  SELECT * INTO STRICT c FROM lineage_draft.run_context WHERE pipeline_run_id=NEW.pipeline_run_id;
  IF p.pipeline_run_id<>NEW.pipeline_run_id OR p.status<>'VALIDATING'
    OR a.kind<>'PROJECTION' OR a.writer_xid<>pg_current_xact_id()
    OR NEW.checked_at<c.t0 OR NEW.checked_at>clock_timestamp()
    OR NEW.authorized_until<=clock_timestamp()
    OR a.document#>>'{body,checked_at_utc}' IS NULL
    OR (a.document#>>'{body,checked_at_utc}')::timestamptz IS DISTINCT FROM NEW.checked_at
    OR a.document#>>'{body,authorized_until_utc}' IS NULL
    OR (a.document#>>'{body,authorized_until_utc}')::timestamptz IS DISTINCT FROM NEW.authorized_until THEN
    RAISE EXCEPTION 'LINEAGE_PUBLICATION_CERTIFICATE_INVALID';
  END IF;
  NEW.writer_xid:=pg_current_xact_id(); RETURN NEW;
END $$;
CREATE TRIGGER validate_publication_evidence BEFORE INSERT ON lineage_draft.publication_evidence
FOR EACH ROW EXECUTE FUNCTION lineage_draft.validate_publication_evidence();
CREATE TRIGGER immutable_publication_evidence BEFORE UPDATE OR DELETE ON lineage_draft.publication_evidence
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_publication_evidence BEFORE TRUNCATE ON lineage_draft.publication_evidence
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();

-- Intent bytes never change; delivery state is a separate, mutable record.
CREATE TABLE lineage_draft.alert_intent (
  intent_id uuid PRIMARY KEY,
  publication_snapshot_id bigint NOT NULL,
  pipeline_run_id uuid NOT NULL,
  projection_hash text NOT NULL,
  publication_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
  channel text NOT NULL CHECK(channel='telegram'),
  recipient_key text NOT NULL CHECK(length(recipient_key)>0),
  semantic_key text NOT NULL CHECK(length(semantic_key)>0),
  signal_node_id text NOT NULL CHECK(length(signal_node_id)>0),
  row_hash text NOT NULL CHECK(row_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  payload_text text NOT NULL CHECK(jsonb_typeof(payload_text::jsonb)='object'),
  payload_hash text NOT NULL CHECK(payload_hash=lineage_draft.sha256_text(payload_text)),
  UNIQUE(channel,recipient_key,semantic_key),
  FOREIGN KEY(publication_snapshot_id,pipeline_run_id,projection_hash,publication_xid)
    REFERENCES lineage_draft.publication_evidence(publication_snapshot_id,pipeline_run_id,projection_hash,writer_xid)
);
CREATE FUNCTION lineage_draft.validate_intent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage_draft.publication_evidence; a lineage_draft.artifact; signal jsonb;
BEGIN
  SELECT * INTO STRICT e FROM lineage_draft.publication_evidence
    WHERE publication_snapshot_id=NEW.publication_snapshot_id;
  IF e.writer_xid<>pg_current_xact_id() OR NEW.publication_xid<>pg_current_xact_id()
     OR NEW.expires_at>e.authorized_until OR NEW.expires_at<=clock_timestamp() THEN
    RAISE EXCEPTION 'OUTBOX_NOT_IN_PUBLICATION_TRANSACTION_OR_EXPIRED';
  END IF;
  SELECT * INTO STRICT a FROM lineage_draft.artifact WHERE pipeline_run_id=e.pipeline_run_id
    AND content_hash=e.projection_hash;
  SELECT s INTO signal FROM jsonb_array_elements(a.document#>'{body,eligible_signals}') s
    WHERE s->>'node_id'=NEW.signal_node_id AND s->>'row_hash'=NEW.row_hash;
  IF signal IS NULL THEN RAISE EXCEPTION 'OUTBOX_SIGNAL_NOT_ELIGIBLE'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER validate_intent BEFORE INSERT ON lineage_draft.alert_intent
FOR EACH ROW EXECUTE FUNCTION lineage_draft.validate_intent();
CREATE TRIGGER immutable_intent BEFORE UPDATE OR DELETE ON lineage_draft.alert_intent
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_intent BEFORE TRUNCATE ON lineage_draft.alert_intent
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();

CREATE TABLE lineage_draft.alert_delivery (
  intent_id uuid PRIMARY KEY REFERENCES lineage_draft.alert_intent(intent_id),
  state text NOT NULL DEFAULT 'PENDING' CHECK(state IN
    ('PENDING','CLAIMED','SENDING','DELIVERED','RETRYABLE','DELIVERY_UNKNOWN','CANCELLED','FAILED')),
  not_before timestamptz NOT NULL DEFAULT clock_timestamp(),
  lease_owner uuid,
  lease_until timestamptz,
  fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
  attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
  provider_receipt jsonb,
  reason text,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX delivery_pending ON lineage_draft.alert_delivery(not_before,intent_id)
  WHERE state IN ('PENDING','RETRYABLE');

CREATE FUNCTION lineage_draft.seed_delivery() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  INSERT INTO lineage_draft.alert_delivery(intent_id) VALUES(NEW.intent_id); RETURN NEW;
END $$;
CREATE TRIGGER seed_delivery AFTER INSERT ON lineage_draft.alert_intent
FOR EACH ROW EXECUTE FUNCTION lineage_draft.seed_delivery();

CREATE FUNCTION lineage_draft.final_publication_check() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e lineage_draft.publication_evidence; p public.publication_snapshot;
BEGIN
  SELECT * INTO STRICT e FROM lineage_draft.publication_evidence
    WHERE publication_snapshot_id=NEW.publication_snapshot_id;
  SELECT * INTO STRICT p FROM public.publication_snapshot WHERE publication_snapshot_id=e.publication_snapshot_id;
  IF p.status<>'PUBLISHED' OR p.published_at IS NULL OR p.pipeline_run_id<>e.pipeline_run_id
     OR clock_timestamp()>=e.authorized_until
     OR NOT EXISTS(SELECT 1 FROM public.publication_head
       WHERE mode=p.mode AND publication_snapshot_id=p.publication_snapshot_id) THEN
    RAISE EXCEPTION 'LINEAGE_PUBLICATION_NOT_FINAL_OR_EXPIRED';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER final_publication_check AFTER INSERT ON lineage_draft.publication_evidence
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage_draft.final_publication_check();

-- Prevent a lineage-aware run from publishing via the old, uncertified path.
-- Enable only in the isolated DB: production integration must call the adapter.
CREATE FUNCTION lineage_draft.require_certificate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; sid bigint;
BEGIN
  sid:=NEW.publication_snapshot_id;
  SELECT pipeline_run_id INTO STRICT rid FROM public.publication_snapshot WHERE publication_snapshot_id=sid;
  IF EXISTS(SELECT 1 FROM lineage_draft.run_context WHERE pipeline_run_id=rid)
     AND NOT EXISTS(SELECT 1 FROM lineage_draft.publication_evidence
       WHERE publication_snapshot_id=sid AND pipeline_run_id=rid) THEN
    RAISE EXCEPTION 'LINEAGE_PUBLICATION_CERTIFICATE_REQUIRED';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER require_lineage_certificate AFTER INSERT OR UPDATE ON public.publication_head
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lineage_draft.require_certificate();

CREATE FUNCTION lineage_draft.protect_bound_run() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF EXISTS(SELECT 1 FROM lineage_draft.run_context WHERE pipeline_run_id=OLD.pipeline_run_id)
     AND (NEW.source_commit IS DISTINCT FROM OLD.source_commit
       OR NEW.warehouse_as_of IS DISTINCT FROM OLD.warehouse_as_of
       OR NEW.pipeline_run_id IS DISTINCT FROM OLD.pipeline_run_id) THEN
    RAISE EXCEPTION 'LINEAGE_BOUND_RUN_CANNOT_DRIFT';
  END IF; RETURN NEW;
END $$;
CREATE TRIGGER protect_lineage_bound_run BEFORE UPDATE ON public.pipeline_run
FOR EACH ROW EXECUTE FUNCTION lineage_draft.protect_bound_run();

-- No runtime roles are granted access in this draft. Production cutover must
-- provide separate capture/publisher/dispatcher roles, fixed search paths,
-- restricted function execution and no owner/superuser application credentials.
-- Neither hashes nor these triggers protect against a database superuser.
REVOKE ALL ON ALL TABLES IN SCHEMA lineage_draft FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage_draft FROM PUBLIC;

CREATE FUNCTION lineage_draft.validate_delivery() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE receipt jsonb; valid boolean:=false;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'PENDING' OR NEW.fence<>0 OR NEW.attempts<>0
       OR NEW.lease_owner IS NOT NULL OR NEW.provider_receipt IS NOT NULL THEN
      RAISE EXCEPTION 'OUTBOX_INVALID_INITIAL_STATE';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.intent_id<>OLD.intent_id THEN RAISE EXCEPTION 'OUTBOX_ID_IMMUTABLE'; END IF;
  valid:=(OLD.state IN ('PENDING','RETRYABLE') AND NEW.state IN ('CLAIMED','CANCELLED','FAILED'))
      OR (OLD.state='CLAIMED' AND NEW.state IN ('SENDING','CANCELLED','PENDING'))
      OR (OLD.state='SENDING' AND NEW.state IN ('DELIVERED','DELIVERY_UNKNOWN','RETRYABLE','FAILED'));
  IF NOT valid THEN RAISE EXCEPTION 'OUTBOX_ILLEGAL_STATE_TRANSITION'; END IF;
  IF NEW.state='CLAIMED' THEN
    IF NEW.fence<>OLD.fence+1 OR NEW.lease_owner IS NULL
       OR NEW.lease_until IS NULL OR NEW.lease_until<=clock_timestamp()
       OR NEW.attempts<>OLD.attempts OR OLD.not_before>clock_timestamp() THEN
      RAISE EXCEPTION 'OUTBOX_INVALID_CLAIM';
    END IF;
  ELSIF NEW.fence<>OLD.fence THEN
    RAISE EXCEPTION 'OUTBOX_FENCE_MISMATCH';
  END IF;
  IF NEW.state='PENDING' AND (OLD.lease_until IS NULL OR OLD.lease_until>clock_timestamp()) THEN
    RAISE EXCEPTION 'OUTBOX_CLAIM_NOT_EXPIRED';
  END IF;
  IF NEW.state='SENDING' THEN
    IF NEW.attempts<>OLD.attempts+1 OR OLD.lease_until<=clock_timestamp()
       OR NEW.lease_owner IS DISTINCT FROM OLD.lease_owner THEN
      RAISE EXCEPTION 'OUTBOX_SEND_WITHOUT_VALID_LEASE';
    END IF;
    IF NOT EXISTS(
      SELECT 1 FROM lineage_draft.alert_intent i
      JOIN lineage_draft.publication_evidence e USING(publication_snapshot_id)
      JOIN public.publication_snapshot p USING(publication_snapshot_id)
      JOIN public.publication_head h ON h.mode=p.mode AND h.publication_snapshot_id=p.publication_snapshot_id
      WHERE i.intent_id=NEW.intent_id AND p.status='PUBLISHED'
        AND i.expires_at>clock_timestamp() AND e.authorized_until>clock_timestamp()) THEN
      RAISE EXCEPTION 'OUTBOX_SEND_PUBLICATION_INVALID_OR_EXPIRED';
    END IF;
  ELSIF NEW.attempts<>OLD.attempts THEN
    RAISE EXCEPTION 'OUTBOX_ATTEMPT_COUNT_INVALID';
  END IF;
  receipt:=NEW.provider_receipt;
  IF NEW.state='DELIVERED' AND (receipt IS NULL
       OR receipt->>'http_status' IS DISTINCT FROM '200'
       OR receipt#>'{body,ok}' IS DISTINCT FROM 'true'::jsonb
       OR jsonb_typeof(receipt#>'{body,result,message_id}') IS DISTINCT FROM 'number'
       OR NOT coalesce((receipt#>>'{body,result,message_id}') ~ '^[1-9][0-9]*$',false)) THEN
    RAISE EXCEPTION 'OUTBOX_APPLICATION_ACK_REQUIRED';
  END IF;
  -- Only the documented flood-control rejection is auto-retryable in this draft.
  -- Other transport errors after a possible send must be quarantined UNKNOWN.
  IF NEW.state='RETRYABLE' THEN
    IF receipt IS NULL OR receipt#>'{body,ok}' IS DISTINCT FROM 'false'::jsonb
       OR receipt#>>'{body,error_code}' IS DISTINCT FROM '429'
       OR NOT coalesce((receipt#>>'{body,parameters,retry_after}') ~ '^[1-9][0-9]*$',false) THEN
      RAISE EXCEPTION 'OUTBOX_CONFIRMED_REJECTION_REQUIRED';
    END IF;
    IF (receipt#>>'{body,parameters,retry_after}')::numeric>86400 THEN
      RAISE EXCEPTION 'OUTBOX_RETRY_INTERVAL_UNSUPPORTED';
    END IF;
    NEW.not_before:=greatest(NEW.not_before,clock_timestamp()+make_interval(
      secs=>(receipt#>>'{body,parameters,retry_after}')::integer));
  END IF;
  NEW.updated_at:=clock_timestamp(); RETURN NEW;
END $$;
CREATE TRIGGER validate_delivery BEFORE INSERT OR UPDATE ON lineage_draft.alert_delivery
FOR EACH ROW EXECUTE FUNCTION lineage_draft.validate_delivery();
CREATE TRIGGER no_delete_delivery BEFORE DELETE ON lineage_draft.alert_delivery
FOR EACH ROW EXECUTE FUNCTION lineage_draft.reject_mutation();
CREATE TRIGGER no_truncate_delivery BEFORE TRUNCATE ON lineage_draft.alert_delivery
FOR EACH STATEMENT EXECUTE FUNCTION lineage_draft.reject_mutation();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage_draft FROM PUBLIC;

-- Set-based recheck of a completed manifest's source identities against the
-- immutable warehouse. This is a proposed additional cost, NOT benchmarked.
-- The arbitrary SQL text saved in a manifest is NEVER dynamically executed.
CREATE FUNCTION lineage_draft.validate_raw_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d jsonb; q jsonb; c lineage_draft.run_context; wanted text[];
        observed timestamptz; lo timestamptz; hi timestamptz; bad boolean;
        expected_catalogue bigint[]; declared_catalogue bigint[];
BEGIN
  d:=((NEW.document_text::jsonb)#>>'{body,sealed_text}')::jsonb; q:=d->'query';
  SELECT * INTO STRICT c FROM lineage_draft.run_context WHERE pipeline_run_id=NEW.pipeline_run_id;
  SELECT array_agg(DISTINCT value ORDER BY value) INTO wanted
    FROM jsonb_array_elements_text(q->'requested_tickers');
  observed:=(q->>'as_of_utc')::timestamptz;
  lo:=(q->>'event_lower_utc')::timestamptz; hi:=(q->>'event_upper_utc')::timestamptz;
  IF wanted IS NULL OR cardinality(wanted)=0 OR observed IS NULL OR hi IS NULL
     OR observed>c.t0 OR hi>observed OR lo>hi
     OR q->>'data_type' IS DISTINCT FROM 'OHLCV'
     OR NOT coalesce(q->>'timeframe' IN ('1d','5m'),false)
     OR (q->>'timeframe'='5m' AND hi+interval '5 minutes'>observed) THEN
    RAISE EXCEPTION 'LINEAGE_RAW_SCOPE_INVALID';
  END IF;
  WITH v AS (
    SELECT DISTINCT ON (instrument_id) * FROM public.instrument_catalogue_revision
    WHERE known_at<=observed AND pg_visible_in_snapshot(writer_xid,c.snapshot_text::pg_snapshot)
    ORDER BY instrument_id,revision_id DESC
  ) SELECT coalesce(array_agg(revision_id ORDER BY revision_id),'{}'::bigint[])
    INTO expected_catalogue FROM v WHERE NOT deleted AND canonical_symbol=ANY(wanted);
  SELECT coalesce(array_agg((r->>'catalogue_revision_id')::bigint
      ORDER BY (r->>'catalogue_revision_id')::bigint),'{}'::bigint[]) INTO declared_catalogue
    FROM jsonb_array_elements(d->'symbols') s
      CROSS JOIN LATERAL jsonb_array_elements(s->'catalogue') r;
  IF expected_catalogue<>declared_catalogue THEN
    RAISE EXCEPTION 'LINEAGE_CATALOGUE_REVISIONS_MISMATCH';
  END IF;
  SELECT EXISTS(
    SELECT 1 FROM jsonb_array_elements(d->'symbols') s
    CROSS JOIN LATERAL jsonb_array_elements(s->'catalogue') r
    JOIN public.instrument_catalogue_revision v ON v.revision_id=(r->>'catalogue_revision_id')::bigint
    WHERE v.instrument_id IS DISTINCT FROM (r->>'instrument_id')::bigint
      OR v.canonical_symbol IS DISTINCT FROM s->>'symbol'
      OR v.writer_xid IS DISTINCT FROM (r->>'writer_xid')::xid8
      OR v.known_at IS DISTINCT FROM (r->>'known_at_utc')::timestamptz
  ) INTO bad;
  IF bad THEN RAISE EXCEPTION 'LINEAGE_CATALOGUE_METADATA_MISMATCH'; END IF;
  WITH v AS (
    SELECT DISTINCT ON (instrument_id) * FROM public.instrument_catalogue_revision
    WHERE known_at<=observed AND pg_visible_in_snapshot(writer_xid,c.snapshot_text::pg_snapshot)
    ORDER BY instrument_id,revision_id DESC
  ), ranked AS (
    SELECT o.observation_id,o.event_timestamp,
      row_number() OVER(PARTITION BY o.instrument_id,o.data_type,o.timeframe,o.event_timestamp
        ORDER BY o.ingested_at DESC,o.observation_id DESC) AS rn
    FROM public.market_observation o JOIN v ON v.instrument_id=o.instrument_id
    WHERE NOT v.deleted AND v.canonical_symbol=ANY(wanted)
      AND o.data_type='OHLCV' AND o.timeframe=q->>'timeframe'
      AND o.event_timestamp<=hi AND (lo IS NULL OR o.event_timestamp>=lo)
      AND o.ingested_at<=observed
      AND pg_visible_in_snapshot(o.writer_xid,c.snapshot_text::pg_snapshot)
  ), expected AS (
    SELECT observation_id,event_timestamp FROM ranked WHERE rn=1
  ), declared AS (
    SELECT (r->>'observation_id')::bigint AS observation_id,
           (r->>'event_timestamp_utc')::timestamptz AS event_timestamp
    FROM jsonb_array_elements(d->'symbols') s
      CROSS JOIN LATERAL jsonb_array_elements(s->'revisions') r
  ) SELECT EXISTS((SELECT * FROM expected EXCEPT ALL SELECT * FROM declared)
                  UNION ALL (SELECT * FROM declared EXCEPT ALL SELECT * FROM expected)) INTO bad;
  IF bad THEN RAISE EXCEPTION 'LINEAGE_SOURCE_INVENTORY_MISMATCH'; END IF;
  -- Validate both IDs and the associated temporal/instrument metadata.
  SELECT EXISTS(
    SELECT 1 FROM jsonb_array_elements(d->'symbols') s
    CROSS JOIN LATERAL jsonb_array_elements(s->'revisions') r
    LEFT JOIN public.market_observation o ON o.observation_id=(r->>'observation_id')::bigint
      AND o.event_timestamp=(r->>'event_timestamp_utc')::timestamptz
    WHERE o.observation_id IS NULL OR o.instrument_id IS DISTINCT FROM (r->>'instrument_id')::bigint
      OR o.writer_xid IS DISTINCT FROM (r->>'writer_xid')::xid8
      OR o.ingested_at IS DISTINCT FROM (r->>'ingested_at_utc')::timestamptz
      OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(s->'catalogue') cat
                    WHERE (cat->>'instrument_id')::bigint=o.instrument_id)
  ) INTO bad;
  IF bad THEN RAISE EXCEPTION 'LINEAGE_SOURCE_REVISION_METADATA_MISMATCH'; END IF;
  SELECT EXISTS(
    SELECT 1 FROM jsonb_array_elements(d->'symbols') s
    WHERE (s->>'state') IS DISTINCT FROM
      CASE WHEN jsonb_array_length(s->'revisions')>0 THEN 'OBSERVATIONS_RETURNED'
           WHEN jsonb_array_length(s->'catalogue')>0 THEN 'NO_OBSERVATIONS_IN_SCOPED_RESULT'
           ELSE 'NO_VISIBLE_CATALOGUE_MATCH' END
      OR (jsonb_array_length(s->'revisions')=0 AND
          s#>>'{absence_certificate,scope_query_hash}' IS DISTINCT FROM d->>'query_hash')
  ) INTO bad;
  IF bad THEN RAISE EXCEPTION 'LINEAGE_ABSENCE_INVENTORY_INVALID'; END IF;
  IF wanted IS DISTINCT FROM (SELECT array_agg(s->>'symbol' ORDER BY s->>'symbol')
       FROM jsonb_array_elements(d->'symbols') s) THEN
    RAISE EXCEPTION 'LINEAGE_SYMBOL_INVENTORY_INVALID';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER validate_raw_manifest BEFORE INSERT ON lineage_draft.artifact
FOR EACH ROW WHEN (NEW.kind='READ') EXECUTE FUNCTION lineage_draft.validate_raw_manifest();
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage_draft FROM PUBLIC;
