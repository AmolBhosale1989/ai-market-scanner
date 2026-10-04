"""Native application integration gate: real partitioned schema and disposable DB only.

The original standalone 18-test archive remains separate. These counterparts use
actual application migrations, authority, producers, roles and dispatcher modules.
No live provider or external alert is enabled by this harness.
"""
from __future__ import annotations
import os
from pathlib import Path
import sys
from uuid import uuid4
from tests.integration import _contract_base as original
import pytest
pytestmark = pytest.mark.postgres_integration
from scanner import control_plane as cp
from scanner.database import close_pools
import pandas as pd
import psycopg
import time
import json
import subprocess
from dataclasses import replace
from datetime import datetime
from scanner.lineage import publication as authority
from scanner.alerts import alert_dispatcher as dispatcher
SOURCE = Path(__file__).resolve().parents[2]


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
        self.db.execute('DROP SCHEMA IF EXISTS lineage CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public')
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
        self.db.commit()
        authority.commit_diagnostics(connect,[raw,view,producer])
        self.raw_evidence,self.view_evidence,self.producer=raw,view,producer
        return producer

    def stage_publication(self,base,seconds=120,physical_rows=True):
        projection=authority.build_projection(self.db.cursor(),base,budget_seconds=seconds)
        return authority.stage_publication(self.db.cursor(),base,projection=projection,physical_rows=physical_rows)

    def test_02_original_read_survives_failed_publication(self):
        base=self.seed_evidence()
        hashes=self.db.execute('SELECT content_hash FROM lineage.artifact ORDER BY content_hash').fetchall()
        contexts=self.count('lineage.run_context')
        self.db.commit()
        self.stage_publication(base);self.db.rollback()
        self.assertEqual(self.db.execute('SELECT content_hash FROM lineage.artifact ORDER BY content_hash').fetchall(),hashes)
        self.assertEqual(self.count('lineage.run_context'),contexts)
        self.assertEqual(len(hashes),3)
        for table in ('public.publication_head','public.publication_snapshot','lineage.publication_evidence',
                      'lineage.alert_intent','lineage.alert_delivery'):
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
        self.assertEqual(self.count('lineage.artifact'),1)

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
        self.assertEqual([n[0] for n in names][-2:],['V0042__lineage_and_transactional_outbox.sql','V0043__lineage_role_grants.sql'])
        self.db.commit()
        cp.migrate()

    def test_21_existing_pointer_and_forensics_survive_failed_replacement(self):
        old_sid,old_intent,_=self.stage_publication(self.seed_evidence())
        self.db.commit()
        self.binding=self.new_context()
        self.seed_evidence()
        before=self.count('lineage.artifact')
        contexts_before=self.count('lineage.run_context')
        sid,iid,_=self.stage_publication(self.producer)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT publication_snapshot_id FROM public.publication_head WHERE mode='production'").fetchone()[0],old_sid)
        self.assertEqual(self.count('lineage.run_context'),contexts_before)
        self.assertEqual(self.count('lineage.artifact'),before)
        self.assertEqual(self.db.execute('SELECT count(*) FROM public.publication_snapshot WHERE publication_snapshot_id=%s',(sid,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM lineage.alert_intent WHERE intent_id=%s',(iid,)).fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM lineage.alert_intent WHERE intent_id=%s',(old_intent,)).fetchone()[0],1)

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
            self.db.execute('SELECT * FROM lineage.artifact')
        self.db.rollback()

    def test_24_actual_control_plane_publisher_requires_adapter(self):
        cp.write_dataset('lineage_probe',pd.DataFrame([{'ticker':'NET'}]),run_id=self.binding.run_id)
        cp.stage_started('integration_probe',1,run_id=self.binding.run_id)
        cp.stage_finished('integration_probe',run_id=self.binding.run_id)
        with self.assertRaises(RuntimeError):
            cp.publish('isolated-integration',['lineage_probe'],run_id=self.binding.run_id)
        self.assertEqual(self.count('public.publication_snapshot'),0)
        self.assertEqual(self.count('lineage.alert_intent'),0)

    def test_25_production_authority_requires_producer_fragment(self):
        base=original.PostgreSQLContracts.seed_evidence(self)
        cur=self.db.cursor();cur.execute('SELECT clock_timestamp()');at=cur.fetchone()[0]
        p=original.Artifact.build(self.binding,'PROJECTION',dict(checked_at_utc=at.isoformat(),
            authorized_until_utc=(at+original.timedelta(seconds=120)).isoformat(),
            eligible_signals=[dict(node_id='signal:NET',row_hash='b'*64)]),[base.content_hash])
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PRODUCER_FRAGMENT_REQUIRED'):
            original.store_artifact(cur,p)
        self.db.rollback()
        self.assertEqual(self.count('lineage.artifact'),1)
        self.assertEqual(self.count('lineage.alert_intent'),0)
        self.evidence={'rejection':'LINEAGE_PRODUCER_FRAGMENT_REQUIRED','raw_diagnostics_retained':1,
                       'projection_committed':False,'intents_committed':0}

    def test_26_projection_must_match_actual_published_dataset(self):
        base=self.seed_evidence()
        self.assertEqual(self.count('lineage.signal_authority'),1)
        self.stage_publication(base,physical_rows=False)
        self.assertEqual(self.count('lineage.alert_intent'),1)
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.count('lineage.alert_intent'),0)
        self.assertEqual(self.count('public.publication_snapshot'),0)
        self.assertEqual(self.count('lineage.artifact'),3)
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
        self.db.execute('SELECT lineage.check_published_signal(%s,%s,%s,%s,%s)',
           (sid,self.binding.run_id,p.content_hash,'signal:NET',p.document()['body']['eligible_signals'][0]['row_hash']))
        self.evidence={'physical_row_matches_producer':True,'snapshot_id':sid}

    def test_28_source_bar_deadline_not_t0_plus_600(self):
        self.seed_evidence(age_seconds=573.8)
        expiry=self.db.execute('SELECT source_expires_at FROM lineage.signal_authority').fetchone()[0]
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
        self.assertEqual(self.db.execute('SELECT state FROM lineage.alert_delivery').fetchone()[0],'SENDING')
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
from tests.integration._contract_base import connect
from scanner.alerts import alert_dispatcher as dispatcher
def transport(payload,timeout_seconds):
 p=Path(sys.argv[1]);p.write_text('SIMULATED_ACCEPTANCE\n');os._exit(73)
dispatcher.dispatch_once(connect,transport,lease_seconds=1)
"""
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(SOURCE)]))
        proc=subprocess.run([sys.executable,'-c',code,str(marker)],env=env,capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,73,proc.stdout+proc.stderr)
        self.assertEqual(marker.read_text(),'SIMULATED_ACCEPTANCE\n')
        self.assertEqual(self.db.execute('SELECT state FROM lineage.alert_delivery').fetchone()[0],'SENDING')
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
        self.assertEqual(self.db.execute("SELECT provider_receipt#>>'{body,result,message_id}' FROM lineage.alert_delivery").fetchone()[0],'123')

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

    def runtime(self,role):
        """Actual nonsuperuser session, not SET ROLE from a superuser connection."""
        login={'scanner_writer':'lineage_scanner_test','dispatcher_worker':'lineage_dispatcher_test'}[role]
        self.db.execute(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{login}') THEN CREATE ROLE {login} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS; END IF; END $$")
        self.db.execute(f'GRANT {role} TO {login}')
        self.db.commit()
        def factory():
            conn=psycopg.connect(host=str(original.SOCKET),port=55482,dbname='lineage_gate_test',user=login,
                connect_timeout=3,options='-c statement_timeout=5000 -c lock_timeout=2000 -c timezone=UTC')
            conn.execute(f'SET ROLE {role}')
            return conn
        return factory

    def test_45_dispatcher_role_cannot_forge_intents(self):
        self.published()
        connect_worker=self.runtime('dispatcher_worker')
        with connect_worker() as db:
            identity=db.execute("SELECT session_user,current_user,(SELECT rolsuper FROM pg_roles WHERE rolname=session_user)").fetchone()
            self.assertEqual(identity,('lineage_dispatcher_test','dispatcher_worker',False))
            with self.assertRaisesRegex(psycopg.errors.InsufficientPrivilege,'permission denied for table alert_intent'):
                db.execute("INSERT INTO lineage.alert_intent(intent_id) VALUES(gen_random_uuid())")
            db.rollback()
        self.evidence={'session_user':identity[0],'current_user':identity[1],'superuser':False,'denied_sqlstate':'42501','forged_intents':0}

    def test_46_cross_run_deduplication_suppresses_spam(self):
        writer=self.runtime('scanner_writer')
        first=self.seed_evidence()
        with writer() as db: sid1,iid1,_=authority.stage_publication(db.cursor(),first,recipient_key='user_1')
        second=self.seed_evidence()
        with writer() as db: sid2,iid2,_=authority.stage_publication(db.cursor(),second,recipient_key='user_1')
        self.assertIsNotNone(iid1);self.assertIsNone(iid2);self.assertNotEqual(sid1,sid2)
        self.assertEqual(self.count('lineage.alert_intent'),1)
        self.evidence={'publications':2,'same_recipient':'user_1','same_semantic_key':'signal:NET',
            'committed_intents':1,'second_staged_intent':None,'runtime_role':'scanner_writer',
            'cooldown_hours':12,'interval':'consecutive commits within cooldown, no fabricated future clock'}

    def real_market_inputs(self):
        """Synthetic OHLCV in the real warehouse; no provider or calendar replay claim."""
        from scanner.lineage.evidence import load_inputs
        from scanner.database import close_pools
        from unittest.mock import patch
        now=self.db.execute('SELECT clock_timestamp()').fetchone()[0]
        end=pd.Timestamp(now).floor('5min')-pd.Timedelta(minutes=5)
        prior=end-pd.Timedelta(days=1)
        self.db.execute("SELECT setval(pg_get_serial_sequence('public.instrument','instrument_id'),(SELECT max(instrument_id) FROM public.instrument))")
        values=[]
        for symbol,gain in [('SPY',0.2),('SKYY',4.0),('SMH',3.0),('NET',6.0),('NVDA',5.0)]:
            row=self.db.execute('SELECT instrument_id FROM public.instrument WHERE canonical_symbol=%s',(symbol,)).fetchone()
            if row is None:
                row=self.db.execute('INSERT INTO public.instrument(canonical_symbol) VALUES(%s) RETURNING instrument_id',(symbol,)).fetchone()
            iid=row[0]
            previous=prior.tz_convert('America/New_York').replace(hour=15,minute=55).tz_convert('UTC')
            values.append((iid,previous.to_pydatetime(),self.warehouse_run,100,100.1,99.9,100,1_000_000))
            for index,at in enumerate(pd.date_range(end-pd.Timedelta(minutes=55),end,freq='5min')):
                price=100+gain*(index+1)/12
                values.append((iid,at.to_pydatetime(),self.warehouse_run,price-0.03,price+0.05,price-0.08,price,4_000_000))
        with self.db.cursor() as cur:
            cur.executemany("""INSERT INTO public.market_observation(instrument_id,data_type,timeframe,event_timestamp,
                ingested_at,warehouse_run_id,provider,open,high,low,close,volume)
                VALUES(%s,'OHLCV','5m',%s,clock_timestamp(),%s,'synthetic-integration',%s,%s,%s,%s,%s)""",values)
        self.db.commit();self.binding=self.new_context()
        writer=self.runtime('scanner_writer')
        with writer() as db:
            dsn=db.info.dsn
        close_pools()
        try:
            with patch.dict(os.environ,{'DATABASE_URL':dsn,'PGSSLMODE':'disable'}):
                batch=load_inputs(self.binding,['SPY','SKYY','SMH','NET','NVDA'])
        finally: close_pools()
        return batch,writer

    def test_47_real_rotation_producer_emits_lineage(self):
        from scanner.producers.rotation_producer import calculate_rotation_eligibility
        from scanner.lineage.presentation import rank_survivors
        batch,writer=self.real_market_inputs()
        rows,fragment=calculate_rotation_eligibility(batch)
        self.assertEqual(fragment.producer_name,'rotation_v4')
        self.assertIn('ROTATION_SKYY_NET',fragment.nodes)
        self.assertNotIn('rotation_rank',rows[0])
        authority.commit_diagnostics(writer,[batch.read,*batch.views.values(),fragment.artifact])
        with writer() as db:
            sid,_,_=authority.stage_publication(db.cursor(),fragment.artifact)
        projected=rank_survivors(rows)
        self.assertTrue(projected)
        self.assertEqual(len(rows),2)
        self.evidence={'producer':'scanner.producers.rotation_producer','shared_core':'scanner.sector_rotation.evaluate_rotation',
            'producer_name':fragment.producer_name,'fragment_hash':fragment.hash,'node_ids':sorted(fragment.nodes),
            'raw_read_boundary':'scanner.bitemporal_warehouse.point_in_time','raw_observations':65,
            'diagnostic_role':'scanner_writer','publication_role':'scanner_writer','snapshot_id':sid,
            'rank_in_eligibility':False,'presentation_rows':len(projected)}

    def test_48_real_momentum_producer_emits_lineage(self):
        from scanner.producers.rotation_producer import calculate_rotation_eligibility
        from scanner.producers.momentum_producer import calculate_momentum_eligibility
        batch,writer=self.real_market_inputs()
        _,rotation=calculate_rotation_eligibility(batch)
        rows,momentum=calculate_momentum_eligibility(batch,rotation,limit=1)
        self.assertEqual(momentum.producer_name,'momentum_v3');self.assertEqual(len(rows),1)
        self.assertIn('MOMENTUM_SKYY_NET',momentum.nodes)
        self.assertIn(rotation.hash,momentum.artifact.parent_hashes)
        authority.commit_diagnostics(writer,[batch.read,*batch.views.values(),rotation.artifact,momentum.artifact])
        with writer() as db: sid,_,_=authority.stage_publication(db.cursor(),momentum.artifact)
        self.evidence={'producer':'scanner.producers.momentum_producer','shared_core':'scanner.momentum_signals.evaluate_momentum',
            'fragment_hash':momentum.hash,'upstream_rotation_hash':rotation.hash,'candidate_limit_preserved':1,
            'selected_ticker':rows[0]['ticker'],'signal':rows[0]['signal'],'snapshot_id':sid,
            'rank_in_eligibility':False,'runtime_role':'scanner_writer'}

    def test_49_writer_cannot_mutate_diagnostics_or_forge_authority(self):
        self.seed_evidence();writer=self.runtime('scanner_writer')
        denied=[]
        for sql in ["UPDATE lineage.artifact SET kind='READ_FAILURE'",'DELETE FROM lineage.artifact',
                    'TRUNCATE lineage.artifact','INSERT INTO lineage.signal_authority(pipeline_run_id) VALUES(gen_random_uuid())']:
            with writer() as db:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege): db.execute(sql)
                db.rollback();denied.append(sql.split()[0])
        self.evidence={'runtime_role':'scanner_writer','denied_operations':denied,'diagnostics_retained':3}

    def test_50_unprivileged_dispatcher_can_complete_valid_delivery(self):
        self.published();worker=self.runtime('dispatcher_worker');calls=[]
        def fake(payload,timeout_seconds):
            calls.append(payload);return 200,{'ok':True,'result':{'message_id':501}}
        result=dispatcher.dispatch_once(worker,fake)
        self.assertEqual(result,'DELIVERED');self.assertEqual(len(calls),1)
        self.evidence={'runtime_role':'dispatcher_worker','result':result,'simulated_transport_calls':1,'external_requests':0}

    def test_51_competing_publications_cannot_both_reserve_key(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        first=self.seed_evidence();second=self.seed_evidence();writer=self.runtime('scanner_writer')
        barrier=threading.Barrier(2)
        def publish(fragment):
            with writer() as db:
                barrier.wait(timeout=3)
                return authority.stage_publication(db.cursor(),fragment,recipient_key='racing_user')[1]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(publish,[first,second]))
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(self.count('lineage.alert_intent'),1)
        self.evidence={'competing_transactions':2,'new_intents':1,'suppressed':1,'runtime_role':'scanner_writer'}

    def test_52_rolled_back_reservation_does_not_suppress_next_run(self):
        writer=self.runtime('scanner_writer');first=self.seed_evidence()
        with self.assertRaisesRegex(psycopg.Error,'LINEAGE_PUBLISHED_ROW_REQUIRED'):
            with writer() as db: authority.stage_publication(db.cursor(),first,physical_rows=False)
        self.assertEqual(self.count('lineage.alert_reservation'),0)
        second=self.seed_evidence()
        with writer() as db: _,iid,_=authority.stage_publication(db.cursor(),second)
        self.assertIsNotNone(iid);self.assertEqual(self.count('lineage.alert_intent'),1)
        self.evidence={'failed_publication_reservations':0,'next_valid_run_intents':1}

    def test_53_deduplication_is_recipient_scoped(self):
        writer=self.runtime('scanner_writer')
        for recipient in ('first_user','second_user'):
            producer=self.seed_evidence()
            with writer() as db:
                _,iid,_=authority.stage_publication(db.cursor(),producer,recipient_key=recipient)
                self.assertIsNotNone(iid)
        self.assertEqual(self.count('lineage.alert_intent'),2)

    def test_54_unknown_delivery_suppresses_next_publication(self):
        self.published();worker=self.runtime('dispatcher_worker')
        self.assertEqual(dispatcher.dispatch_once(worker,lambda *a,**k:(None,None)),'DELIVERY_UNKNOWN')
        producer=self.seed_evidence();writer=self.runtime('scanner_writer')
        with writer() as db: _,iid,_=authority.stage_publication(db.cursor(),producer)
        self.assertIsNone(iid);self.assertEqual(self.count('lineage.alert_intent'),1)
        self.evidence={'prior_state':'DELIVERY_UNKNOWN','next_publication_intents':0}

    def test_55_presentation_cannot_add_candidates_or_change_scores(self):
        from scanner.lineage.presentation import rank_survivors
        rows=[{'ticker':'NET','dependency_node_id':'ROTATION_SKYY_NET','rotation_leader_score':80},
              {'ticker':'NVDA','dependency_node_id':'ROTATION_SMH_NVDA','rotation_leader_score':79}]
        saved=json.dumps(rows,sort_keys=True)
        filtered=[rows[1]]
        display=rank_survivors(filtered,limit=10)
        self.assertEqual([r['ticker'] for r in display],['NVDA'])
        self.assertEqual(display[0]['presentation_rank'],1)
        self.assertEqual(json.dumps(rows,sort_keys=True),saved)
        self.assertEqual(rank_survivors(rows,limit=0),[])

    def test_56_dispatcher_cannot_become_scanner_role(self):
        worker=self.runtime('dispatcher_worker')
        with worker() as db:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):db.execute('SET ROLE scanner_writer')
            db.rollback()
        self.evidence={'dispatcher_to_scanner_role_escalation':'DENIED','sqlstate':'42501'}
