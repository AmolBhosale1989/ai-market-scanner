-- Owner-only migration. Runtime identities must never own tables or schema.
DO $roles$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['scanner_writer','dispatcher_worker'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=name) THEN
   EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',name);
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=name AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls OR rolcanlogin)) THEN
   RAISE EXCEPTION 'LINEAGE_UNSAFE_EXISTING_ROLE: %',name;
  END IF;
 END LOOP;
 IF pg_has_role('scanner_writer','dispatcher_worker','MEMBER') OR pg_has_role('dispatcher_worker','scanner_writer','MEMBER') THEN
   RAISE EXCEPTION 'LINEAGE_RUNTIME_ROLES_NOT_SEPARATE';
 END IF;
END $roles$;
REVOKE ALL ON SCHEMA lineage FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA lineage FROM PUBLIC,scanner_writer,dispatcher_worker;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA lineage FROM PUBLIC,scanner_writer,dispatcher_worker;
GRANT USAGE ON SCHEMA lineage,public TO scanner_writer,dispatcher_worker;
GRANT SELECT,INSERT ON lineage.run_context,lineage.artifact,lineage.publication_evidence,lineage.alert_intent TO scanner_writer;
GRANT SELECT ON lineage.signal_authority,lineage.alert_delivery TO scanner_writer;
-- Trigger-owned materialization and reservations: no direct runtime write grants.
GRANT EXECUTE ON FUNCTION lineage.sha256_text(text) TO scanner_writer,dispatcher_worker;
GRANT EXECUTE ON FUNCTION lineage.check_published_signal(bigint,uuid,text,text,text) TO dispatcher_worker;
GRANT SELECT ON public.market_observation,public.instrument,public.instrument_catalogue_revision,
 public.instrument_dimension_history,public.warehouse_run_log,public.frozen_catalogue TO scanner_writer;
GRANT INSERT ON public.frozen_catalogue TO scanner_writer;
GRANT SELECT,INSERT,UPDATE ON public.pipeline_run,public.pipeline_stage,public.dataset_version,
 public.publication_snapshot,public.publication_head TO scanner_writer;
GRANT SELECT,INSERT,DELETE ON public.dataset_row,public.publication_dataset TO scanner_writer;
GRANT USAGE,SELECT ON SEQUENCE public.dataset_version_dataset_version_id_seq,
 public.publication_snapshot_publication_snapshot_id_seq TO scanner_writer;
GRANT SELECT ON lineage.alert_intent,lineage.alert_delivery,lineage.publication_evidence,
 public.publication_snapshot,public.publication_head TO dispatcher_worker;
GRANT UPDATE(state,not_before,lease_owner,lease_until,fence,attempts,provider_receipt,reason,updated_at)
 ON lineage.alert_delivery TO dispatcher_worker;
-- Explicit assertions protect future edits from silently granting writer authority.
DO $verify$
BEGIN
 IF has_table_privilege('dispatcher_worker','lineage.alert_intent','INSERT')
 OR has_table_privilege('dispatcher_worker','lineage.artifact','INSERT')
 OR has_table_privilege('scanner_writer','lineage.artifact','UPDATE')
 OR has_table_privilege('scanner_writer','lineage.artifact','DELETE')
 OR has_table_privilege('scanner_writer','lineage.artifact','TRUNCATE')
 OR has_table_privilege('scanner_writer','lineage.signal_authority','INSERT')
 OR has_table_privilege('scanner_writer','lineage.alert_reservation','UPDATE') THEN
   RAISE EXCEPTION 'LINEAGE_RUNTIME_PRIVILEGE_ASSERTION_FAILED';
 END IF;
END $verify$;
