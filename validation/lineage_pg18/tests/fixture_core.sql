-- Synthetic schema fixture with the relevant frozen public-column contracts.
-- NOT the repository migration chain and NOT its partitioned OHLCV deployment.
CREATE TABLE public.pipeline_run (
 pipeline_run_id uuid PRIMARY KEY, mode text NOT NULL, source_commit text NOT NULL,
 warehouse_as_of timestamptz NOT NULL, status text NOT NULL);
CREATE TABLE public.frozen_catalogue (
 pipeline_run_id uuid NOT NULL REFERENCES public.pipeline_run,
 as_of_utc timestamptz NOT NULL, pg_snapshot text NOT NULL, dataset_name text NOT NULL,
 records jsonb NOT NULL, content_hash text NOT NULL,
 PRIMARY KEY(pipeline_run_id,as_of_utc,pg_snapshot,dataset_name));
CREATE TABLE public.publication_snapshot (
 publication_snapshot_id bigserial PRIMARY KEY,
 pipeline_run_id uuid UNIQUE NOT NULL REFERENCES public.pipeline_run,
 mode text NOT NULL, status text NOT NULL, published_at timestamptz,
 manifest_hash text, metadata jsonb NOT NULL DEFAULT '{}');
CREATE TABLE public.publication_head (
 mode text PRIMARY KEY, publication_snapshot_id bigint NOT NULL REFERENCES public.publication_snapshot,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE public.instrument_catalogue_revision (
 revision_id bigserial PRIMARY KEY, instrument_id bigint NOT NULL,
 canonical_symbol text NOT NULL, known_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 deleted boolean NOT NULL DEFAULT false, writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id());
CREATE TABLE public.market_observation (
 observation_id bigserial, instrument_id bigint NOT NULL, data_type text NOT NULL,
 timeframe text NOT NULL, event_timestamp timestamptz NOT NULL,
 ingested_at timestamptz NOT NULL, writer_xid xid8 NOT NULL DEFAULT pg_current_xact_id(),
 open numeric,high numeric,low numeric,close numeric,volume numeric,
 PRIMARY KEY(observation_id,event_timestamp));
CREATE FUNCTION public.immutable_snapshot_input() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'SNAPSHOT_INPUT_APPEND_ONLY'; END IF;
 NEW.writer_xid:=pg_current_xact_id(); RETURN NEW;
END $$;
CREATE TRIGGER protect_market BEFORE INSERT OR UPDATE OR DELETE ON public.market_observation
 FOR EACH ROW EXECUTE FUNCTION public.immutable_snapshot_input();
CREATE TRIGGER protect_catalogue BEFORE INSERT OR UPDATE OR DELETE ON public.instrument_catalogue_revision
 FOR EACH ROW EXECUTE FUNCTION public.immutable_snapshot_input();
