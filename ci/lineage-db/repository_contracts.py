"""Application-schema integration gate; never run against a shared database.

Reuses the original 18 assertions with full repository migrations and appropriate
fixture inserts. The hardened candidate is tested with explicit producer declarations and physical
publication rows. The live producer pipeline is not enabled by this harness.
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
import time
import json
import subprocess
from dataclasses import replace
from datetime import datetime
sys.path.insert(0, str(Path(__file__).resolve().parent / "hardening"))
import authority
import dispatcher


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

    def seed_evidence(self, age_seconds=480):
        # Real committed market revision, captured before the new run's T0.
        self.db.execute("""INSERT INTO public.market_observation
          (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,provider,open,high,low,close,volume)
          VALUES(1,'OHLCV','5m',clock_timestamp()-make_interval(secs=>%s),
            clock_timestamp()-interval '1 second',%s,'fixture',10,11,9,10,100)""",
          (age_seconds+300,self.warehouse_run))
        self.db.commit()
        self.binding=self.new_context()
        raw=self.artifact(include_observation=True)
        doc=json.loads(raw.document()['body']['sealed_text'])
        refs=[dict(observation_id=x['observation_id'],event_timestamp_utc=x['event_timestamp_utc'])
              for s in doc['symbols'] for x in s['revisions']]
        view=authority.view_for_read(raw,refs)
        self.signal_row={'ticker':'NET','dependency_node_id':'signal:NET','rotation_score':80.0,
                         'meets_alert_threshold':True}
        producer=authority.declared_producer(self.binding,[view],[self.signal_row])
        self.db.commit()  # End diagnostic construction reads; the helper owns evidence commit.
        authority.commit_diagnostics(connect,[raw,view,producer])
        self.raw_evidence,self.view_evidence,self.producer=raw,view,producer
        return producer

    def stage_publication(self,base,seconds=120,physical_rows=True):
        projection=authority.build_projection(self.db.cursor(),base,budget_seconds=seconds)
        return authority.stage_publication(self.db.cursor(),base,projection=projection,physical_rows=physical_rows)

    def test_02_original_read_survives_failed_publication(self):
        base=self.seed_evidence()
        hashes=self.db.execute('SELECT content_hash FROM lineage_draft.artifact ORDER BY content_hash').fetchall()
        contexts=self.count('lineage_draft.run_context')
        self.db.commit()
        self.stage_publication(base);self.db.rollback()
        self.assertEqual(self.db.execute('SELECT content_hash FROM lineage_draft.artifact ORDER BY content_hash').fetchall(),hashes)
        self.assertEqual(self.count('lineage_draft.run_context'),contexts)
        self.assertEqual(len(hashes),3)
        for table in ('public.publication_head','public.publication_snapshot','lineage_draft.publication_evidence',
                      'lineage_draft.alert_intent','lineage_draft.alert_delivery'):
            self.assertEqual(self.count(table),0)
        self.evidence={'diagnostic_artifacts_retained':3,'publication_intents_retained':0,'two_transactions':True}

    def test_16_late_intent_append_rejected(self):
        sid,_,p=self.stage_publication(self.seed_evidence());self.db.commit()
        signal=p.document()['body']['eligible_signals'][0]
        with self.assertRaisesRegex(psycopg.Error,'OUTBOX_NOT_IN_PUBLICATION_TRANSACTION_OR_EXPIRED'):
            original.record_alert_intent(self.db.cursor(),sid,p,recipient_key='other',semantic_key='late',
                node_id=signal['node_id'],row_hash=signal['row_hash'],
                expires_at=datetime.fromisoformat(p.document()['body']['authorized_until_utc']),payload={})

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
        self.assertEqual(len(names),10)
        self.db.commit()
        cp.migrate()

    def test_21_existing_pointer_and_forensics_survive_failed_replacement(self):
        old_sid,old_intent,_=self.stage_publication(self.seed_evidence())
        self.db.commit()
        self.binding=self.new_context()
        self.seed_evidence()
        before=self.count('lineage_draft.artifact')
        contexts_before=self.count('lineage_draft.run_context')
        sid,iid,_=self.stage_publication(self.producer)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT publication_snapshot_id FROM public.publication_head WHERE mode='production'").fetchone()[0],old_sid)
        self.assertEqual(self.count('lineage_draft.run_context'),contexts_before)
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
        # Deliberately bypass the valid fixture helper; READ cannot authorize a signal.
        base=original.PostgreSQLContracts.seed_evidence(self)
        cur=self.db.cursor();cur.execute('SELECT clock_timestamp()');at=cur.fetchone()[0]
        p=original.Artifact.build(self.binding,'PROJECTION',dict(checked_at_utc=at.isoformat(),
            authorized_until_utc=(at+original.timedelta(seconds=120)).isoformat(),
            eligible_signals=[dict(node_id='signal:NET',row_hash='b'*64)]),[base.content_hash])
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PRODUCER_FRAGMENT_REQUIRED'):
            original.store_artifact(cur,p)
        self.db.rollback()
        self.assertEqual(self.count('lineage_draft.artifact'),1)
        self.assertEqual(self.count('lineage_draft.alert_intent'),0)
        self.evidence={'rejection':'LINEAGE_PRODUCER_FRAGMENT_REQUIRED','raw_diagnostics_retained':1,
                       'projection_committed':False,'intents_committed':0}

    def test_26_projection_must_match_actual_published_dataset(self):
        # VALID producer required: prevents A from accidentally making B pass.
        base=self.seed_evidence()
        self.assertEqual(self.count('lineage_draft.signal_authority'),1)
        self.stage_publication(base,physical_rows=False)
        self.assertEqual(self.count('lineage_draft.alert_intent'),1)  # staged, not committed
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.count('lineage_draft.alert_intent'),0)
        self.assertEqual(self.count('public.publication_snapshot'),0)
        self.assertEqual(self.count('lineage_draft.artifact'),3)
        self.evidence={'valid_producer_authority':True,'rejection_at':'COMMIT',
                       'rejection':'LINEAGE_PUBLISHED_ROW_REQUIRED','diagnostics_retained':3,'intents_committed':0}

    def published(self):
        result=self.stage_publication(self.seed_evidence())
        self.db.commit()
        return result

    def test_27_success_has_exact_physical_row(self):
        sid,iid,p=self.published()
        row=self.db.execute("""SELECT dr.payload FROM public.publication_dataset pd
            JOIN public.dataset_row dr USING(dataset_version_id) WHERE pd.publication_snapshot_id=%s""",(sid,)).fetchone()[0]
        self.assertEqual(row,self.signal_row)
        self.db.execute('SELECT lineage_draft.check_published_signal(%s,%s,%s,%s,%s)',
           (sid,self.binding.run_id,p.content_hash,'signal:NET',p.document()['body']['eligible_signals'][0]['row_hash']))
        self.evidence={'physical_row_matches_producer':True,'snapshot_id':sid}

    def test_28_source_bar_deadline_not_t0_plus_600(self):
        self.seed_evidence(age_seconds=573.8)
        expiry=self.db.execute('SELECT source_expires_at FROM lineage_draft.signal_authority').fetchone()[0]
        bar=self.db.execute("SELECT max(event_timestamp)+interval '5 minutes' FROM public.market_observation").fetchone()[0]
        self.assertEqual(expiry,bar+original.timedelta(seconds=600))
        remaining=(expiry-self.binding.t0_utc).total_seconds()
        self.assertTrue(20<remaining<27)
        self.evidence={'shelf_life_at_t0_seconds':remaining,'source_bar_end_utc':bar.isoformat(),'expires_at_utc':expiry.isoformat()}

    def test_29_expiry_extension_rejected(self):
        base=self.seed_evidence(age_seconds=573.8)
        cur=self.db.cursor();cur.execute('SELECT clock_timestamp()');at=cur.fetchone()[0]
        signal=base.document()['body']['signals'][0]
        p=original.Artifact.build(self.binding,'PROJECTION',dict(checked_at_utc=at.isoformat(),
            authorized_until_utc=(self.binding.t0_utc+original.timedelta(seconds=600)).isoformat(),
            eligible_signals=[dict(node_id=signal['node_id'],row_hash=signal['row_hash'])]),[base.content_hash])
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_SOURCE_EXPIRY_EXTENSION'):original.store_artifact(cur,p)

    def test_30_fabricated_signal_with_valid_parent_rejected(self):
        base=self.seed_evidence()
        p=authority.build_projection(self.db.cursor(),base)
        body=p.document()['body'];body['eligible_signals'][0]['row_hash']='b'*64
        forged=original.Artifact.build(self.binding,'PROJECTION',body,[base.content_hash])
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_SIGNAL_NOT_DECLARED_BY_PRODUCER'):
            original.store_artifact(self.db.cursor(),forged)

    def test_31_mismatched_physical_payload_rejected_at_commit(self):
        self.stage_publication(self.seed_evidence())
        self.db.execute("UPDATE public.dataset_row SET payload=jsonb_set(payload,'{rotation_score}','99')")
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):self.db.commit()

    def test_32_wrong_snapshot_dataset_rejected(self):
        sid,_,_=self.stage_publication(self.seed_evidence())
        self.db.execute('DELETE FROM public.publication_dataset WHERE publication_snapshot_id=%s',(sid,))
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):self.db.commit()

    def test_33_duplicate_sending_claim_unavailable_after_commit(self):
        self.published();item=dispatcher.claim(connect)
        self.assertTrue(dispatcher.begin_sending(connect,item))
        self.assertIsNone(dispatcher.claim(connect))
        self.assertEqual(self.db.execute('SELECT state FROM lineage_draft.alert_delivery').fetchone()[0],'SENDING')
        self.evidence={'sending_committed':True,'competing_claim':None}

    def test_34_expired_sending_quarantined_and_late_ack_fenced(self):
        self.published();item=dispatcher.claim(connect,1)
        self.assertTrue(dispatcher.begin_sending(connect,item));time.sleep(1.1)
        sweep=dispatcher.recover(connect)
        self.assertEqual(sweep[0][1],'DELIVERY_UNKNOWN')
        self.assertEqual(dispatcher.resolve(connect,item,200,{'ok':True,'result':{'message_id':12}}),'STALE_RESOLUTION')
        self.assertIsNone(dispatcher.claim(connect))
        self.evidence={'recovery_state':sweep[0][1],'late_success_result':'STALE_RESOLUTION','reclaimed':False}

    def test_35_expired_unsent_claim_requeues_with_new_fence(self):
        self.published();old=dispatcher.claim(connect,1);time.sleep(1.1)
        self.assertEqual(dispatcher.recover(connect)[0][1],'PENDING')
        new=dispatcher.claim(connect)
        self.assertGreater(new.fence,old.fence)
        self.assertFalse(dispatcher.begin_sending(connect,old))
        self.assertTrue(dispatcher.begin_sending(connect,new))
        self.evidence={'old_fence':old.fence,'new_fence':new.fence,'stale_worker_authorized':False}

    def test_36_worker_process_death_after_simulated_acceptance(self):
        self.published()
        marker=Path(os.environ['LINEAGE_TEST_SOCKET']).parent/('accepted-'+uuid4().hex)
        code=r"""import sys,os
from pathlib import Path
from postgres_contracts import connect
import dispatcher
def transport(payload,timeout_seconds):
 p=Path(sys.argv[1]);p.write_text('SIMULATED_ACCEPTANCE\n');os._exit(73)
dispatcher.dispatch_once(connect,transport,lease_seconds=1)
"""
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(PACKAGE/'tests'),str(PACKAGE),
            str(PACKAGE/'upstream/warehouse_lineage_phase2'),str(Path(__file__).parent/'hardening')]))
        proc=subprocess.run([sys.executable,'-c',code,str(marker)],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,73,proc.stdout+proc.stderr)
        self.assertEqual(marker.read_text(),'SIMULATED_ACCEPTANCE\n')
        self.assertEqual(self.db.execute('SELECT state FROM lineage_draft.alert_delivery').fetchone()[0],'SENDING')
        self.db.commit();time.sleep(1.1)
        self.assertEqual(dispatcher.recover(connect)[0][1],'DELIVERY_UNKNOWN')
        calls=[]
        self.assertIsNone(dispatcher.dispatch_once(connect,lambda *a,**k:calls.append(1)))
        self.assertEqual(calls,[])
        self.evidence={'child_exit_code':73,'simulated_acceptances':1,'external_requests':0,
                       'durable_state_before_recovery':'SENDING','after_recovery':'DELIVERY_UNKNOWN','redelivery_calls':0}

    def test_37_http_200_without_application_ack_unknown(self):
        self.published()
        state=dispatcher.dispatch_once(connect,lambda *a,**k:(200,{'ok':False}))
        self.assertEqual(state,'DELIVERY_UNKNOWN')

    def test_38_application_ack_delivered(self):
        self.published();calls=[]
        def transport(*a,**k):
            calls.append(k['timeout_seconds']);return 200,{'ok':True,'result':{'message_id':123}}
        self.assertEqual(dispatcher.dispatch_once(connect,transport),'DELIVERED')
        self.assertEqual(len(calls),1);self.assertTrue(0<calls[0]<=5)
        self.assertEqual(self.db.execute("SELECT provider_receipt#>>'{body,result,message_id}' FROM lineage_draft.alert_delivery").fetchone()[0],'123')

    def test_39_confirmed_429_bounded_retry(self):
        self.published()
        status=dispatcher.dispatch_once(connect,lambda *a,**k:(429,{'ok':False,'error_code':429,'parameters':{'retry_after':1}}))
        self.assertEqual(status,'RETRYABLE');self.assertIsNone(dispatcher.claim(connect))
        time.sleep(1.1);self.assertIsNotNone(dispatcher.claim(connect))

    def test_40_retry_cannot_outlive_source(self):
        self.published()
        status=dispatcher.dispatch_once(connect,lambda *a,**k:(429,{'ok':False,'error_code':429,'parameters':{'retry_after':1000}}))
        self.assertEqual(status,'FAILED');self.assertIsNone(dispatcher.claim(connect))

    def test_41_claim_from_superseded_publication_blocked(self):
        self.published();item=dispatcher.claim(connect)
        self.db.execute("DELETE FROM public.publication_head WHERE mode='production'");self.db.commit()
        with self.assertRaisesRegex(psycopg.Error,'OUTBOX_SEND_PUBLICATION_INVALID_OR_EXPIRED'):
            dispatcher.begin_sending(connect,item)

    def test_42_wrong_owner_resolution_fenced(self):
        self.published();item=dispatcher.claim(connect);dispatcher.begin_sending(connect,item)
        wrong=replace(item,owner=str(uuid4()))
        self.assertEqual(dispatcher.resolve(connect,wrong,200,{'ok':True,'result':{'message_id':1}}),'STALE_RESOLUTION')
        self.assertEqual(dispatcher.resolve(connect,item,200,{'ok':True,'result':{'message_id':1}}),'DELIVERED')

    def test_43_same_transaction_diagnostic_and_publication_rejected(self):
        self.seed_evidence()
        # A new producer is inserted but deliberately not diagnostically committed.
        row=dict(self.signal_row,rotation_score=81.0)
        fresh=authority.declared_producer(self.binding,[self.view_evidence],[row])
        original.store_artifact(self.db.cursor(),fresh)
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_DIAGNOSTIC_COMMIT_REQUIRED'):
            self.stage_publication(fresh)

    def test_44_send_authorization_rechecks_physical_payload(self):
        self.published();item=dispatcher.claim(connect);dispatcher.begin_sending(connect,item)
        self.db.execute("UPDATE public.dataset_row SET payload='{}'::jsonb");self.db.commit()
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):
            dispatcher.pre_send(connect,item)
