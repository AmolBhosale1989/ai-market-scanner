"""Native namespace counterparts of the original database contracts.

The full-schema subclass owns setup. Only an authorized disposable Unix socket
is accepted. The original standalone 18-test archive is preserved separately.
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
import psycopg
from scanner.lineage.models import Binding,ReadSeal,digest
from scanner.lineage.persistence import (Artifact,read_artifact,store_artifact,register_context,
 record_publication_certificate,record_alert_intent,claim_one,canonical)
SOCKET=Path(os.environ.get('LINEAGE_TEST_SOCKET','/not-authorized'))
if (not str(SOCKET).startswith('/tmp/market-hunt-lineage-') or not SOCKET.is_dir()
 or not (SOCKET.parent/'LOCAL_ONLY_AUTHORIZATION').is_file()):
 raise SystemExit('REFUSED: disposable Unix socket authorization required')

def connect():
 return psycopg.connect(host=str(SOCKET),port=55482,dbname='lineage_gate_test',user='lineage_test',
  connect_timeout=3,options='-c statement_timeout=5000 -c lock_timeout=1000 -c timezone=UTC')
SHA=os.environ['GITHUB_SHA']
HASHES={k:'a'*64 for k in ('master_universe','live_universe','tradable_universe')}

class PostgreSQLContracts(unittest.TestCase):
 def setUp(self):raise RuntimeError('Use full-schema subclass')
 def tearDown(self):
  self.other.rollback();self.db.rollback();self.other.close();self.db.close()
 def new_context(self):raise RuntimeError('Use full-schema context writer')
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
 def stage_publication(self,base,seconds=120):raise RuntimeError('Use native publisher in subclass')
 def count(self,table,db=None):
  c=(db or self.db).cursor();c.execute(f'SELECT count(*) FROM {table}');return c.fetchone()[0]
 def test_01_schema_created(self):self.assertEqual(self.count('lineage.run_context'),1)
 def test_02_original_read_survives_failed_publication(self):
  self.stage_publication(self.seed_evidence());self.db.rollback()
  self.assertEqual(self.count('lineage.artifact'),1)
  for t in ('public.publication_head','public.publication_snapshot','lineage.publication_evidence','lineage.alert_intent','lineage.alert_delivery'):
   self.assertEqual(self.count(t),0)
 def test_03_atomic_success(self):
  self.stage_publication(self.seed_evidence());self.db.commit()
  for t in ('public.publication_head','lineage.publication_evidence','lineage.alert_intent','lineage.alert_delivery'):self.assertEqual(self.count(t),1)
 def test_04_uncommitted_intent_invisible(self):
  self.stage_publication(self.seed_evidence());self.assertEqual(self.count('lineage.alert_intent',self.other),0)
  self.db.commit();self.other.commit();self.assertEqual(self.count('lineage.alert_intent',self.other),1)
 def test_05_derived_artifact_is_allowed_after_t0(self):
  self.seed_evidence();c=self.db.cursor()
  c.execute('SELECT pg_visible_in_snapshot(writer_xid,%s::pg_snapshot) FROM lineage.artifact',(self.binding.pg_snapshot,))
  self.assertIs(c.fetchone()[0],False)
 def test_06_artifact_immutable_update(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage.artifact SET kind='READ_FAILURE'")
 def test_07_artifact_immutable_delete(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute('DELETE FROM lineage.artifact')
 def test_08_artifact_immutable_truncate(self):
  self.seed_evidence()
  with self.assertRaises(psycopg.Error):self.db.execute('TRUNCATE lineage.artifact CASCADE')
 def test_09_bound_run_clock_cannot_drift(self):
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE public.pipeline_run SET warehouse_as_of=warehouse_as_of+interval '1 second'")
 def test_10_binding_mismatch_rejected(self):
  base=self.artifact();wrong=replace(self.binding,t0_utc=self.binding.t0_utc+timedelta(seconds=1))
  a=Artifact.build(wrong,'READ',base.document()['body'])
  with self.assertRaises(psycopg.Error):store_artifact(self.db.cursor(),a)
 def test_11_source_late_commit_excluded(self):raise RuntimeError('Subclass must implement full observation row')
 def test_12_concurrent_catalogue_revision_uses_original(self):raise RuntimeError('Subclass must implement full catalogue row')
 def test_13_no_head_without_evidence_certificate(self):
  c=self.db.cursor();c.execute("INSERT INTO public.publication_snapshot(pipeline_run_id,mode,status,published_at) VALUES(%s,'production','PUBLISHED',clock_timestamp()) RETURNING publication_snapshot_id",(self.binding.run_id,))
  c.execute("INSERT INTO public.publication_head VALUES('production',%s,clock_timestamp())",(c.fetchone()[0],))
  with self.assertRaises(psycopg.Error):self.db.commit()
 def test_14_expiry_before_final_constraint_rolls_back(self):
  self.stage_publication(self.seed_evidence(),seconds=0.2);time.sleep(0.25)
  with self.assertRaises(psycopg.Error):self.db.commit()
  self.db.rollback();self.assertEqual(self.count('lineage.alert_intent'),0)
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
  self.db.execute("UPDATE lineage.alert_delivery SET state='SENDING',attempts=attempts+1 WHERE intent_id=%s",(iid,));self.db.commit()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage.alert_delivery SET state='DELIVERED',provider_receipt=%s::jsonb WHERE intent_id=%s",('{"http_status":200}',iid))
 def test_18_delivery_unknown_is_terminal(self):
  _,iid,_=self.stage_publication(self.seed_evidence());self.db.commit()
  claim_one(self.db.cursor(),str(uuid4()));self.db.commit()
  self.db.execute("UPDATE lineage.alert_delivery SET state='SENDING',attempts=attempts+1 WHERE intent_id=%s",(iid,));self.db.commit()
  self.db.execute("UPDATE lineage.alert_delivery SET state='DELIVERY_UNKNOWN' WHERE intent_id=%s",(iid,));self.db.commit()
  with self.assertRaises(psycopg.Error):self.db.execute("UPDATE lineage.alert_delivery SET state='RETRYABLE' WHERE intent_id=%s",(iid,))
