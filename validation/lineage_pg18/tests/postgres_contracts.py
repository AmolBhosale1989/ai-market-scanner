"""REAL PostgreSQL tests, unexecuted in the authoring environment.

Use run_isolated_postgres.sh. Refuses arbitrary URLs, TCP connections, production
names, and external DATABASE_URL. Only the shell-created local UNIX socket is used.
The public schema fixture is deliberately a subset; full repo CI remains separate.
"""
from pathlib import Path
import json
import os
import sys
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'upstream'/'warehouse_lineage_phase2')]
try:
 import psycopg
except ImportError:
 print('NOT RUN: psycopg and an isolated PostgreSQL server are required.',file=sys.stderr)
 raise SystemExit(2)
from dependency_suppression import Binding
from pit_evidence import ReadSeal,digest
from persistence import (Artifact,read_artifact,store_artifact,register_context,
 record_publication_certificate,record_alert_intent,claim_one,canonical)

SOCKET=Path(os.environ.get('LINEAGE_TEST_SOCKET','/not-authorized'))
if (not str(SOCKET).startswith('/tmp/market-hunt-lineage-') or
    not SOCKET.is_dir() or not (SOCKET.parent/'LOCAL_ONLY_AUTHORIZATION').is_file()):
 raise SystemExit('REFUSED: only a shell-created /tmp/market-hunt-lineage-*/socket is authorized.')


def connect():
 return psycopg.connect(host=str(SOCKET),port=55482,dbname='lineage_gate_test',user='lineage_test',
   connect_timeout=3,options='-c statement_timeout=5000 -c lock_timeout=1000 -c timezone=UTC')

SHA='25c2c8bfca2cb648bf265e0baed5ffe25a6fec69'
HASHES={k:'a'*64 for k in ('master_universe','live_universe','tradable_universe')}

class PostgreSQLContracts(unittest.TestCase):
 def setUp(self):
  self.db=connect(); self.other=connect()
  with self.db.cursor() as c:
   c.execute('DROP SCHEMA IF EXISTS lineage_draft CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public')
   c.execute((ROOT/'tests'/'fixture_core.sql').read_text())
   c.execute((ROOT/'sql'/'lineage_gate_DRAFT.sql').read_text())
   c.execute("INSERT INTO public.instrument_catalogue_revision(instrument_id,canonical_symbol,known_at) VALUES(1,'NET',clock_timestamp()-interval '1 day')")
  self.db.commit()
  self.binding=self.new_context()
 def tearDown(self):
  self.other.rollback();self.db.rollback();self.other.close();self.db.close()
 def new_context(self):
  c=self.db.cursor(); c.execute('SELECT clock_timestamp(),pg_current_snapshot()::text');t0,snapshot=c.fetchone()
  binding=Binding(str(uuid4()),t0,snapshot,SHA,'LOCAL_TEST_ONLY')
  c.execute("INSERT INTO public.pipeline_run VALUES(%s,'production',%s,%s,'STARTED')",(binding.run_id,SHA,t0))
  for name,digest_value in HASHES.items():
   c.execute('INSERT INTO public.frozen_catalogue VALUES(%s,%s,%s,%s,%s::jsonb,%s)',
     (binding.run_id,t0,snapshot,name,'[]',digest_value))
  register_context(c,binding,HASHES);self.db.commit();return binding
 def artifact(self,binding=None,include_observation=False):
  b=binding or self.binding;c=self.db.cursor()
  c.execute('''SELECT revision_id,instrument_id,canonical_symbol,known_at,writer_xid::text
   FROM public.instrument_catalogue_revision WHERE instrument_id=1 AND known_at<=%s
   AND pg_visible_in_snapshot(writer_xid,%s::pg_snapshot) ORDER BY revision_id DESC LIMIT 1''',(b.t0_utc,b.pg_snapshot))
  rev,iid,sym,known,xid=c.fetchone()
  q=dict(requested_tickers=['NET'],as_of_utc=b.t0_utc.isoformat(),event_lower_utc=(b.t0_utc-timedelta(days=2)).isoformat(),
   event_upper_utc=(b.t0_utc-timedelta(minutes=5)).isoformat(),timeframe='5m',data_type='OHLCV')
  rows=[]
  if include_observation:
   # Intentionally includes newest committed rows without snapshot filtering:
   # delayed-commit tests use this to prove that the DB rejects those references.
   c.execute('SELECT observation_id,instrument_id,writer_xid::text,event_timestamp,ingested_at FROM public.market_observation')
   for oid,oi,wx,ev,ing in c.fetchall():
    rows.append(dict(observation_id=str(oid),instrument_id=str(oi),writer_xid=wx,
      event_timestamp_utc=ev.isoformat(),ingested_at_utc=ing.isoformat()))
  state='OBSERVATIONS_RETURNED' if rows else 'NO_OBSERVATIONS_IN_SCOPED_RESULT'
  d=dict(binding=b.record(),outcome='COMPLETE_RESULT',query=q,query_hash=digest(q),
   raw_result={},raw_result_hash=digest({}),catalogue_result={},catalogue_result_hash=digest({}),
   symbols=[dict(symbol='NET',state=state,catalogue=[dict(instrument_id=str(iid),catalogue_revision_id=str(rev),
     writer_xid=xid,known_at_utc=known.isoformat())],revisions=rows,
     absence_certificate=None if rows else dict(scope_query_hash=digest(q)))])
  return read_artifact(ReadSeal.build(d,1000000),b)
 def seed_evidence(self):
  a=self.artifact();store_artifact(self.db.cursor(),a);self.db.commit();return a
 def stage_publication(self,base,seconds=120):
  c=self.db.cursor();c.execute('SELECT clock_timestamp()');at=c.fetchone()[0]
  # Fixture authority, NOT real calendar/strategy revalidation.
  projection=Artifact.build(self.binding,'PROJECTION',dict(checked_at_utc=at.isoformat(),
   authorized_until_utc=(at+timedelta(seconds=seconds)).isoformat(),
   eligible_signals=[dict(node_id='signal:NET',row_hash='b'*64)]),[base.content_hash])
  c.execute("INSERT INTO public.publication_snapshot(pipeline_run_id,mode,status) VALUES(%s,'production','VALIDATING') RETURNING publication_snapshot_id",(self.binding.run_id,))
  sid=c.fetchone()[0];record_publication_certificate(c,sid,projection)
  iid=record_alert_intent(c,sid,projection,recipient_key='local-recipient',semantic_key=str(uuid4()),
   node_id='signal:NET',row_hash='b'*64,expires_at=at+timedelta(seconds=seconds),payload={'local_fixture':True})
  c.execute("UPDATE public.publication_snapshot SET status='PUBLISHED',published_at=clock_timestamp() WHERE publication_snapshot_id=%s",(sid,))
  c.execute("INSERT INTO public.publication_head(mode,publication_snapshot_id) VALUES('production',%s)",(sid,))
  return sid,iid,projection
 def count(self,table,db=None):
  # Identifiers here are test constants, never user input.
  c=(db or self.db).cursor();c.execute(f'SELECT count(*) FROM {table}');return c.fetchone()[0]
 def test_01_schema_created(self):
  self.assertEqual(self.count('lineage_draft.run_context'),1)
 def test_02_original_read_survives_failed_publication(self):
  base=self.seed_evidence();self.stage_publication(base);self.db.rollback()
  self.assertEqual(self.count('lineage_draft.artifact'),1)
  for t in ('public.publication_head','public.publication_snapshot','lineage_draft.publication_evidence',
            'lineage_draft.alert_intent','lineage_draft.alert_delivery'):
   self.assertEqual(self.count(t),0)
 def test_03_atomic_success(self):
  self.stage_publication(self.seed_evidence());self.db.commit()
  for t in ('public.publication_head','lineage_draft.publication_evidence','lineage_draft.alert_intent','lineage_draft.alert_delivery'):
   self.assertEqual(self.count(t),1)
 def test_04_uncommitted_intent_invisible(self):
  self.stage_publication(self.seed_evidence())
  self.assertEqual(self.count('lineage_draft.alert_intent',self.other),0)
  self.db.commit();self.other.commit()
  self.assertEqual(self.count('lineage_draft.alert_intent',self.other),1)
 def test_05_derived_artifact_is_allowed_after_t0(self):
  self.seed_evidence();c=self.db.cursor()
  c.execute('SELECT pg_visible_in_snapshot(writer_xid,%s::pg_snapshot) FROM lineage_draft.artifact',(self.binding.pg_snapshot,))
  self.assertIs(c.fetchone()[0],False)
 def test_06_artifact_immutable_update(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage_draft.artifact SET kind='READ_FAILURE'")
 def test_07_artifact_immutable_delete(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute('DELETE FROM lineage_draft.artifact')
 def test_08_artifact_immutable_truncate(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute('TRUNCATE lineage_draft.artifact CASCADE')
 def test_09_bound_run_clock_cannot_drift(self):
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE public.pipeline_run SET warehouse_as_of=warehouse_as_of+interval '1 second'")
 def test_10_binding_mismatch_rejected(self):
  base=self.artifact();wrong=replace(self.binding,t0_utc=self.binding.t0_utc+timedelta(seconds=1))
  a=Artifact.build(wrong,'READ',base.document()['body'])
  with self.assertRaises(psycopg.Error):store_artifact(self.db.cursor(),a)
 def test_11_source_late_commit_excluded(self):
  c=self.other.cursor();c.execute("INSERT INTO public.market_observation(instrument_id,data_type,timeframe,event_timestamp,ingested_at) VALUES(1,'OHLCV','5m',clock_timestamp()-interval '10 minutes',clock_timestamp()-interval '9 minutes')")
  self.binding=self.new_context();self.other.commit()
  # An absence under the older snapshot remains valid even after this commit.
  store_artifact(self.db.cursor(),self.artifact());self.db.commit()
  with self.assertRaises(psycopg.Error):store_artifact(self.db.cursor(),self.artifact(include_observation=True))
 def test_12_concurrent_catalogue_revision_uses_original(self):
  self.other.execute("INSERT INTO public.instrument_catalogue_revision(instrument_id,canonical_symbol,known_at) VALUES(1,'NET',clock_timestamp()-interval '1 hour')")
  self.binding=self.new_context();self.other.commit()
  store_artifact(self.db.cursor(),self.artifact());self.db.commit()
  self.assertEqual(self.count('lineage_draft.artifact'),1)
 def test_13_no_head_without_evidence_certificate(self):
  c=self.db.cursor();c.execute("INSERT INTO public.publication_snapshot(pipeline_run_id,mode,status,published_at) VALUES(%s,'production','PUBLISHED',clock_timestamp()) RETURNING publication_snapshot_id",(self.binding.run_id,))
  c.execute("INSERT INTO public.publication_head VALUES('production',%s,clock_timestamp())",(c.fetchone()[0],))
  with self.assertRaises(psycopg.Error):self.db.commit()
 def test_14_expiry_before_final_constraint_rolls_back(self):
  self.stage_publication(self.seed_evidence(),seconds=0.2);time.sleep(0.25)
  with self.assertRaises(psycopg.Error):self.db.commit()
  self.db.rollback();self.assertEqual(self.count('lineage_draft.alert_intent'),0)
 def test_15_two_workers_cannot_claim_same_locked_row(self):
  self.stage_publication(self.seed_evidence());self.db.commit()
  first=claim_one(self.db.cursor(),str(uuid4()));second=claim_one(self.other.cursor(),str(uuid4()))
  self.assertIsNotNone(first);self.assertIsNone(second)
 def test_16_late_intent_append_rejected(self):
  sid,_,p=self.stage_publication(self.seed_evidence());self.db.commit()
  with self.assertRaises(psycopg.Error):record_alert_intent(self.db.cursor(),sid,p,
   recipient_key='other',semantic_key='late',node_id='signal:NET',row_hash='b'*64,
   expires_at=__import__('datetime').datetime.fromisoformat(p.document()['body']['authorized_until_utc']),payload={})
 def test_17_http_200_alone_not_delivery(self):
  _,iid,_=self.stage_publication(self.seed_evidence());self.db.commit()
  claim_one(self.db.cursor(),str(uuid4()));self.db.commit()
  self.db.execute("UPDATE lineage_draft.alert_delivery SET state='SENDING',attempts=attempts+1 WHERE intent_id=%s",(iid,));self.db.commit()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage_draft.alert_delivery SET state='DELIVERED',provider_receipt=%s::jsonb WHERE intent_id=%s",('{"http_status":200}',iid))
 def test_18_delivery_unknown_is_terminal(self):
  _,iid,_=self.stage_publication(self.seed_evidence());self.db.commit()
  claim_one(self.db.cursor(),str(uuid4()));self.db.commit()
  self.db.execute("UPDATE lineage_draft.alert_delivery SET state='SENDING',attempts=attempts+1 WHERE intent_id=%s",(iid,));self.db.commit()
  self.db.execute("UPDATE lineage_draft.alert_delivery SET state='DELIVERY_UNKNOWN' WHERE intent_id=%s",(iid,));self.db.commit()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage_draft.alert_delivery SET state='RETRYABLE' WHERE intent_id=%s",(iid,))

if __name__=='__main__':unittest.main(verbosity=2)
