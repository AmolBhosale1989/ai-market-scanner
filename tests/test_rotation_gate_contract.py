import pandas as pd
from scanner import warehouse_gate as gate
from scanner.config import ROTATION_REQUIRED_SYMBOLS, ROTATION_OPTIONAL_SYMBOLS, THEME_INTRADAY_MAX_AGE_MINUTES
from scanner.sector_rotation import THEME_ETFS


def test_required_rotation_benchmarks_remain_strict(monkeypatch):
    monkeypatch.setattr(gate, 'MASTER_UNIVERSE_MINIMUM', 1)
    tiers = gate.build_tiers(['SPY'], ['SPY'])
    critical = next(t for t in tiers if t.name == 'CRITICAL_INTRADAY')
    assert set(ROTATION_REQUIRED_SYMBOLS) | set(ROTATION_OPTIONAL_SYMBOLS) == {'SPY', *THEME_ETFS.values()}
    assert 'SKYY' not in critical.symbols
    assert len(critical.symbols) == 21
    assert set(ROTATION_REQUIRED_SYMBOLS) <= set(critical.symbols)
    assert critical.minimum_coverage == 1
    assert critical.max_age_minutes == THEME_INTRADAY_MAX_AGE_MINUTES == 10
    frame = pd.DataFrame([dict(ticker=s, bars=120, invalid_bars=0,
        event_timestamp='2026-09-25T16:40:00Z', ingested_at='2026-09-25T16:46:01Z')
        for s in critical.symbols])
    frame.loc[frame.ticker == 'SPY', 'event_timestamp'] = '2026-09-25T16:30:00Z'
    result = gate.evaluate_tier(critical, frame, now_utc=pd.Timestamp('2026-09-25T16:47:05Z'))
    assert result['status'] == 'FAIL'
    assert result['stale_sample'] == ['SPY']


def test_stale_skyy_is_tolerated_by_theme_gate(monkeypatch):
    monkeypatch.setattr(gate, 'MASTER_UNIVERSE_MINIMUM', 1)
    tiers = gate.build_tiers(['SPY'], ['SPY'])
    theme = next(t for t in tiers if t.name == 'THEME_INTRADAY')
    assert 'SKYY' in theme.symbols
    frame = pd.DataFrame([dict(ticker=s, bars=120, invalid_bars=0,
        event_timestamp='2026-09-25T16:40:00Z', ingested_at='2026-09-25T16:46:01Z')
        for s in theme.symbols])
    frame.loc[frame.ticker == 'SKYY', 'event_timestamp'] = '2026-09-25T16:30:00Z'
    result = gate.evaluate_tier(theme, frame, now_utc=pd.Timestamp('2026-09-25T16:47:05Z'))
    assert result['status'] == 'PASS'
    assert result['stale_sample'] == ['SKYY']


def test_quarantined_skyy_produces_no_cloud_rotation_signal(monkeypatch):
    from scanner import sector_rotation as rotation
    monkeypatch.setattr(rotation, '_load_rotation_history',
                        lambda: (['SPY', 'SKYY', 'MSFT'], {'SPY': pd.DataFrame()}))
    monkeypatch.setattr(rotation, 'latest_frame_session', lambda frame: '2026-09-25')
    monkeypatch.setattr(rotation, '_stats', lambda frame, ticker, session:
                        {'day_change_pct': 0} if ticker == 'SPY' else None)
    monkeypatch.setattr(rotation, 'write_dataset', lambda *a, **kw: None)
    themes, leaders = rotation.run()
    assert themes.empty
    assert leaders.empty
