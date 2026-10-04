"""Application-schema integration gate; never run against a shared database.

Reuses the original 18 assertions with full repository migrations and appropriate
fixture inserts. Additional production-entry requirements intentionally remain
blocking until the producer/publisher authority is integrated.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys
from uuid import uuid4

PACKAGE = Path(os.environ['LINEAGE_VERIFIED_PACKAGE']).resolve()
SOURCE = Path(os.environ['LINEAGE_REPOSITORY_SOURCE']).resolve()
sys.path.insert(0, str(SOURCE))
spec = importlib.util.spec_from_file_location('original_contracts', PACKAGE / 'tests/postgres_contracts.py')
original = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = original
spec.loader.exec_module(original)
from scanner import control_plane as cp
from scanner.database import close_pools
import pandas as pd
import psycopg


def connect():
    db = original.connect()
    row = db.execute("SELECT current_database(),current_user,inet_server_addr(),current_setting('cluster_name')").fetchone()
    if row != ('lineage_gate_test', 'lineage_test', None, os.environ['LINEAGE_CLUSTER_ID']):
        db.close()
        raise RuntimeError('ISOLATED_DATABASE_IDENTITY_MISMATCH')
    return db


class RepositoryContracts(original.PostgreSQLContracts):
    def setUp(self):
        close_pools()
        self.db, self.other = connect(), connect()
        self.other.rollback()
        self.db.execute('DROP SCHEMA IF EXISTS lineage_draft CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        self.db.commit()
        cp.migrate()
        with self.db.cursor() as c:
            c.execute("INSERT INTO public.instrument(instrument_id,canonical_symbol) VALUES(1,'NET')")
            self.warehouse_run = str(uuid4())
            c.execute("""INSERT INTO public.warehouse_run_log
                (warehouse_run_id,provider,request_type,requested_at,status)
                VALUES(%s,'fixture','OHLCV',clock_timestamp(),'AVAILABLE')""", (self.warehouse_run,))
        self.db.commit()
        self.binding = self.new_context()

    def tearDown(self):
        super().tearDown()
        close_pools()

    def new_context(self):
        c = self.db.cursor()
        c.execute('SELECT clock_timestamp(),pg_current_snapshot()::text')
        t0, snapshot = c.fetchone()
        binding = original.Binding(str(uuid4()), t0, snapshot, original.SHA, 'LOCAL_TEST_ONLY')
        c.execute("""INSERT INTO public.pipeline_run
            (pipeline_run_id,mode,source_commit,warehouse_as_of,status)
            VALUES(%s,'production',%s,%s,'STARTED')""", (binding.run_id,original.SHA,t0))
        for name, value in original.HASHES.items():
            c.execute('''INSERT INTO public.frozen_catalogue
                (pipeline_run_id,as_of_utc,pg_snapshot,dataset_name,records,content_hash)
                VALUES(%s,%s,%s,%s,%s::jsonb,%s)''', (binding.run_id,t0,snapshot,name,'[]',value))
        original.register_context(c,binding,original.HASHES)
        self.db.commit()
        return binding

    def stage_publication(self, base, seconds=120):
        # Preserve the original asserted behavior; use the actual head upsert.
        c = self.db.cursor()
        c.execute('SELECT clock_timestamp()')
        at = c.fetchone()[0]
        projection = original.Artifact.build(self.binding,'PROJECTION',dict(
            checked_at_utc=at.isoformat(),
            authorized_until_utc=(at+original.timedelta(seconds=seconds)).isoformat(),
            eligible_signals=[dict(node_id='signal:NET',row_hash='b'*64)]),[base.content_hash])
        c.execute("""INSERT INTO public.publication_snapshot(pipeline_run_id,mode,status)
            VALUES(%s,'production','VALIDATING') RETURNING publication_snapshot_id""",(self.binding.run_id,))
        sid=c.fetchone()[0]
        original.record_publication_certificate(c,sid,projection)
        iid=original.record_alert_intent(c,sid,projection,recipient_key='local-recipient',
            semantic_key=str(uuid4()),node_id='signal:NET',row_hash='b'*64,
            expires_at=at+original.timedelta(seconds=seconds),payload={'local_fixture':True})
        c.execute("UPDATE public.publication_snapshot SET status='PUBLISHED',published_at=clock_timestamp() WHERE publication_snapshot_id=%s",(sid,))
        c.execute("""INSERT INTO public.publication_head(mode,publication_snapshot_id)
            VALUES('production',%s) ON CONFLICT(mode) DO UPDATE
            SET publication_snapshot_id=EXCLUDED.publication_snapshot_id,updated_at=clock_timestamp()""",(sid,))
        return sid,iid,projection

    def test_11_source_late_commit_excluded(self):
        self.other.execute("""INSERT INTO public.market_observation
            (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,provider,open,high,low,close,volume)
            VALUES(1,'OHLCV','5m',clock_timestamp()-interval '10 minutes',
            clock_timestamp()-interval '9 minutes',%s,'fixture',10,11,9,10,100)""",(self.warehouse_run,))
        self.binding=self.new_context()
        self.other.commit()
        original.store_artifact(self.db.cursor(),self.artifact())
        self.db.commit()
        with self.assertRaises(psycopg.Error):
            original.store_artifact(self.db.cursor(),self.artifact(include_observation=True))

    def test_12_concurrent_catalogue_revision_uses_original(self):
        self.other.execute("""INSERT INTO public.instrument_catalogue_revision
            (instrument_id,canonical_symbol,asset_class,active,known_at)
            VALUES(1,'NET','EQUITY',true,clock_timestamp()-interval '1 hour')""")
        self.binding=self.new_context()
        self.other.commit()
        original.store_artifact(self.db.cursor(),self.artifact())
        self.db.commit()
        self.assertEqual(self.count('lineage_draft.artifact'),1)

    def test_19_real_partitioned_composite_primary_key(self):
        row=self.db.execute("""SELECT c.relkind,pg_get_constraintdef(k.oid)
            FROM pg_class c JOIN pg_constraint k ON k.conrelid=c.oid AND k.contype='p'
            WHERE c.oid='public.market_observation'::regclass""").fetchone()
        self.assertEqual(row,('p','PRIMARY KEY (observation_id, event_timestamp)'))
        self.assertGreaterEqual(self.db.execute("SELECT count(*) FROM pg_inherits WHERE inhparent='public.market_observation'::regclass").fetchone()[0],1)

    def test_20_all_migrations_registered_and_repeatable(self):
        names=self.db.execute('SELECT migration_name FROM public.schema_migration ORDER BY migration_name').fetchall()
        self.assertEqual([x[0] for x in names],sorted(p.name for p in (SOURCE/'sql').glob('*.sql')))
        self.assertEqual(len(names),9)
        self.db.commit()
        cp.migrate()

    def test_21_existing_pointer_and_forensics_survive_failed_replacement(self):
        old_sid,old_intent,_=self.stage_publication(self.seed_evidence())
        self.db.commit()
        self.binding=self.new_context()
        self.seed_evidence()
        before=self.count('lineage_draft.artifact')
        sid,iid,_=self.stage_publication(self.artifact())
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT publication_snapshot_id FROM public.publication_head WHERE mode='production'").fetchone()[0],old_sid)
        self.assertEqual(self.count('lineage_draft.run_context'),2)
        self.assertEqual(self.count('lineage_draft.artifact'),before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM public.publication_snapshot WHERE publication_snapshot_id=%s',(sid,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM lineage_draft.alert_intent WHERE intent_id=%s',(iid,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM lineage_draft.alert_intent WHERE intent_id=%s',(old_intent,)).fetchone()[0],1)

    def test_22_commit_guards_cover_head_insert_and_update(self):
        rows=self.db.execute("""SELECT tgname,tgdeferrable,tginitdeferred,pg_get_triggerdef(oid)
            FROM pg_trigger WHERE tgname IN ('require_lineage_certificate','final_publication_check')""").fetchall()
        self.assertEqual(len(rows),2)
        for name,deferrable,deferred,definition in rows:
            self.assertTrue(deferrable and deferred)
            if name=='require_lineage_certificate': self.assertIn('INSERT OR UPDATE',definition)

    def test_23_ungranted_runtime_role_cannot_authorize(self):
        role='lineage_runtime_'+uuid4().hex
        self.db.execute(f'CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT')
        self.db.execute(f'SET LOCAL ROLE {role}')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.db.execute('SELECT * FROM lineage_draft.artifact')
        self.db.rollback()

    def test_24_actual_control_plane_publisher_requires_adapter(self):
        # Actual application transaction writer in a nonproduction test mode;
        # not a test of the 109-dataset production DAG or warehouse freshness.
        cp.write_dataset('lineage_probe',pd.DataFrame([{'ticker':'NET'}]),run_id=self.binding.run_id)
        cp.stage_started('integration_probe',1,run_id=self.binding.run_id)
        cp.stage_finished('integration_probe',run_id=self.binding.run_id)
        with self.assertRaises(RuntimeError):
            cp.publish('isolated-integration',['lineage_probe'],run_id=self.binding.run_id)
        self.assertEqual(self.count('public.publication_snapshot'),0)
        self.assertEqual(self.count('lineage_draft.alert_intent'),0)

    def test_25_production_authority_requires_producer_fragment(self):
        # A hash-linked READ alone must NOT authorize an invented signal.
        # Blocking requirement, not xfail: exposes unimplemented integration.
        base=self.seed_evidence()
        with self.assertRaises((psycopg.Error,RuntimeError)):
            self.stage_publication(base)
            self.db.commit()

    def test_26_projection_must_match_actual_published_dataset(self):
        # Claimed eligible node/row hash with no publication_dataset backing
        # must not be actionable. The reduced fixture did not enforce this.
        with self.assertRaises((psycopg.Error,RuntimeError)):
            self.stage_publication(self.seed_evidence())
            self.db.commit()
