import pandas as pd
from scanner import warehouse_gate as gate
from scanner.config import ROTATION_REQUIRED_SYMBOLS, THEME_INTRADAY_MAX_AGE_MINUTES
from scanner.sector_rotation import THEME_ETFS


def test_all_rotation_benchmarks_are_strict_upstream(monkeypatch):
    monkeypatch.setattr(gate, 'MASTER_UNIVERSE_MINIMUM', 1)
    tiers = gate.build_tiers(['SPY'], ['SPY'])
    critical = next(t for t in tiers if t.name == 'CRITICAL_INTRADAY')
    assert set(ROTATION_REQUIRED_SYMBOLS) == {'SPY', *THEME_ETFS.values()}
    assert set(ROTATION_REQUIRED_SYMBOLS) <= set(critical.symbols)
    assert critical.minimum_coverage == 1
    assert critical.max_age_minutes == THEME_INTRADAY_MAX_AGE_MINUTES == 10
    frame = pd.DataFrame([dict(ticker=s, bars=120, invalid_bars=0,
        event_timestamp='2026-09-25T16:40:00Z', ingested_at='2026-09-25T16:46:01Z')
        for s in critical.symbols])
    frame.loc[frame.ticker == 'SKYY', 'event_timestamp'] = '2026-09-25T16:30:00Z'
    result = gate.evaluate_tier(critical, frame, now_utc=pd.Timestamp('2026-09-25T16:47:05Z'))
    assert result['status'] == 'FAIL'
    assert result['stale_sample'] == ['SKYY']
