from contextlib import nullcontext

import pandas as pd
import pytest

from scanner import bitemporal_warehouse as pit, broad_breakout, live, warehouse


@pytest.mark.parametrize('period,anchor,expected', [
    ('3d', '2026-09-27T16:00Z', '2026-09-24T00:00Z'),
    ('1mo', '2024-03-31T16:00Z', '2024-02-29T00:00Z'),
    ('1y', '2024-02-29T16:00Z', '2023-02-28T00:00Z'),
    ('2wk', '2026-09-27T16:00Z', '2026-09-13T00:00Z'),
])
def test_calendar_period_bounds(period, anchor, expected):
    assert pit.period_start(period, anchor) == pd.Timestamp(expected)


@pytest.mark.parametrize('period', ['max', '0d', '-1d', '3h', '', None])
def test_invalid_period_rejected(period):
    with pytest.raises(ValueError, match='PERIOD_INVALID'):
        pit.period_start(period, '2026-09-25T16:00Z')


def test_frames_pushes_period_into_sql_without_losing_t0(monkeypatch):
    import scanner.consumer_snapshot as snapshot
    anchor = pd.Timestamp('2026-09-25T16:00Z')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: anchor)
    monkeypatch.setattr(pit, '_connect', lambda: nullcontext(object()))
    captured = []
    def query(sql, conn, params):
        captured.append((sql, params))
        return pd.DataFrame([dict(ticker='AAA', event_timestamp=anchor-pd.Timedelta(minutes=5),
            ingested_at=anchor, open=10, high=12, low=9, close=11, volume=100)])
    monkeypatch.setattr(pd, 'read_sql_query', query)
    result = warehouse.frames(['AAA'], period='3d', interval='5m')
    sql, params = captured[0]
    assert 'AND o.event_timestamp >= %s' in sql
    assert 'AND o.ingested_at <= %s' in sql
    assert params[3:] == (anchor, anchor, pd.Timestamp('2026-09-22T00:00Z'))
    assert result['AAA'].iloc[0]['Close'] == 11


def test_live_replay_uses_t0_even_when_wall_clock_changes(monkeypatch):
    anchor = pd.Timestamp('2026-09-25T16:00Z')
    monkeypatch.setattr(live, 'consumer_anchor', lambda: anchor)
    class ForbiddenClock:
        @staticmethod
        def now(*args):
            pytest.fail('strategy consulted wall clock')
    monkeypatch.setattr(live, 'datetime', ForbiddenClock)
    frame = pd.DataFrame(dict(Open=[100, 101], High=[102, 103], Low=[99, 100],
        Close=[101, 102], Volume=[1000, 1500]),
        index=pd.to_datetime(['2026-09-25T14:00Z', '2026-09-25T15:55Z']))
    result = live.analyze_live_candidate('AAA', 100, 'ARMED', 0, 2, 10, False,
        history_frame=frame, as_of=anchor)
    assert result['live_status'] == 'LIVE'
    assert result['live_session_date'] == '2026-09-25'
    with pytest.raises(RuntimeError, match='ANCHOR_MISMATCH'):
        live.analyze_live_candidate('AAA', 100, 'ARMED', 0, 2, 10, False,
            history_frame=frame, as_of=anchor+pd.Timedelta(minutes=10))


def test_production_cannot_evaluate_without_t0(monkeypatch):
    monkeypatch.setenv('PRODUCTION_RUN_ID', 'production-test')
    monkeypatch.setattr(live, 'consumer_anchor', lambda: None)
    with pytest.raises(RuntimeError, match='ANCHOR_REQUIRED'):
        live.enrich_live_candidates(pd.DataFrame([dict(ticker='AAA', stage='ARMED', final_score=90)]))


def test_enrichment_passes_same_anchor_to_every_candidate(monkeypatch):
    anchor = pd.Timestamp('2026-09-25T16:00Z')
    monkeypatch.setattr(live, 'consumer_anchor', lambda: anchor)
    monkeypatch.setattr(live, 'warehouse_frames', lambda *a, **k: {'AAA': pd.DataFrame(), 'BBB': pd.DataFrame()})
    seen = []
    def analyze(**kwargs):
        seen.append(kwargs['as_of'])
        return {}
    monkeypatch.setattr(live, 'analyze_live_candidate', analyze)
    live.enrich_live_candidates(pd.DataFrame([
        dict(ticker='AAA', stage='ARMED', final_score=90),
        dict(ticker='BBB', stage='ARMED', final_score=80)]))
    assert seen == [anchor, anchor]


def test_broad_selection_ranks_before_limit(monkeypatch):
    universe = pd.DataFrame([dict(ticker=t, avg_dollar_volume20=v, adr20_pct=v,
        max_up_day_30d_pct=v, ret20_pct=v) for t, v in [('LOW', 1), ('HIGH', 3), ('MID', 2)]])
    monkeypatch.setattr(broad_breakout, 'read_dataset', lambda *a: universe)
    monkeypatch.setattr(broad_breakout, 'warehouse_history', lambda *a, **k: pd.DataFrame())
    selected = []
    def frames(tickers, **kwargs):
        selected.extend(tickers)
        return {}
    monkeypatch.setattr(broad_breakout, 'warehouse_frames', frames)
    broad_breakout.run(scan_limit=2)
    assert selected == ['HIGH', 'MID']


@pytest.mark.postgres_integration
def test_postgres_period_excludes_old_events_and_future_revisions(monkeypatch):
    import os
    import uuid
    import scanner.consumer_snapshot as snapshot
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    anchor = pd.Timestamp('2026-09-25T16:00Z')
    monkeypatch.setattr(snapshot, 'consumer_anchor', lambda: anchor)
    symbol = 'GATE3_' + uuid.uuid4().hex.upper()
    rid = pit.start_run('test', 'gate3_period', {})
    lower = pd.Timestamp('2026-09-22T00:00Z')
    frame = pd.DataFrame([
        dict(ticker=symbol, event_timestamp=event, ingested_at=anchor-pd.Timedelta(minutes=1),
             Open=10., High=12., Low=9., Close=11., Volume=100.)
        for event in [lower-pd.Timedelta(minutes=5), lower, anchor-pd.Timedelta(minutes=5)]
    ])
    pit.ingest_observations(frame, rid, 'test', 'OHLCV', '5m')
    revision = frame.iloc[-1:].copy()
    revision['ingested_at'] = anchor+pd.Timedelta(minutes=1)
    revision['Close'] = 12.
    pit.ingest_observations(revision, rid, 'test', 'OHLCV', '5m')
    result = pit.point_in_time(pit.PointInTimeRequirement('test', (symbol,), timeframe='5m', period='3d'))
    assert result['event_timestamp'].tolist() == [lower, anchor-pd.Timedelta(minutes=5)]
    assert result['close'].tolist() == [11., 11.]
