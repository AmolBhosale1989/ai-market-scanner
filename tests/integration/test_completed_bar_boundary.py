"""Regression for the actual SQL bound, not a rewritten manifest timestamp."""
import json
import unittest
import pandas as pd
from tests.integration import test_lineage_contracts as fixture_module
from scanner.lineage.evidence import load_inputs
from scanner.lineage.persistence import store_artifact


class CompletedBarContracts(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.RepositoryContracts('test_47_real_rotation_producer_emits_lineage')
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_57_visible_incomplete_bar_is_excluded_by_actual_query(self):
        f=self.fixture
        _,writer=f.real_market_inputs()
        oid,started=f.db.execute("""INSERT INTO public.market_observation
          (instrument_id,data_type,timeframe,event_timestamp,ingested_at,warehouse_run_id,provider,open,high,low,close,volume)
          VALUES(1,'OHLCV','5m',clock_timestamp()-interval '1 minute',clock_timestamp(),%s,
                 'synthetic-incomplete',200,201,199,200,100)
          RETURNING observation_id,event_timestamp""",(f.warehouse_run,)).fetchone()
        f.db.commit()
        f.binding=f.new_context()
        self.assertTrue(f.db.execute('SELECT pg_visible_in_snapshot(writer_xid,%s::pg_snapshot) FROM public.market_observation WHERE observation_id=%s AND event_timestamp=%s',(f.binding.pg_snapshot,oid,started)).fetchone()[0])
        batch=load_inputs(f.binding,['SPY','SKYY','SMH','NET','NVDA'],connect=writer)
        raw=json.loads(batch.read.document()['body']['sealed_text'])
        declared={r['observation_id'] for s in raw['symbols'] for r in s['revisions']}
        self.assertNotIn(str(oid),declared)
        self.assertEqual(len(declared),65)
        upper=pd.Timestamp(raw['query']['event_upper_utc'])
        self.assertLessEqual(upper+pd.Timedelta(minutes=5),pd.Timestamp(f.binding.t0_utc))
        self.assertLess(upper,pd.Timestamp(started))
        with writer() as db:store_artifact(db.cursor(),batch.read)
        self.evidence={'incomplete_revision_visible_at_t0':True,'excluded_by_sql_scope':True,
            'returned_completed_observations':len(declared),'incomplete_observation_id':oid,
            'query_upper_utc':upper.isoformat(),'t0_utc':f.binding.t0_utc.isoformat(),
            'real_database_manifest_validation':'PASS','runtime_role':'scanner_writer'}
