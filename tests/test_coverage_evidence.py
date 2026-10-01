import pandas as pd
import pytest

from scanner import control_plane as cp, warehouse_gate as gate


@pytest.fixture
def evidence(monkeypatch):
    at = pd.Timestamp('2026-10-01T14:17:00Z')
    tier = gate.CoverageTier('CRITICAL_INTRADAY', ('AAA', 'BBB'), '5m', 1., 1, 10)
    universe = pd.DataFrame({'ticker': list(tier.symbols)})
    monkeypatch.setattr(gate, 'verify_health', lambda: None)
    monkeypatch.setattr(gate, 'build_tiers', lambda *_: [tier])
    monkeypatch.setattr('scanner.consumer_snapshot.capture_boundary', lambda **_: (at, '10:10:'))
    monkeypatch.setattr('scanner.catalogue_snapshot.read_frozen_catalogue', lambda *a, **k: universe.copy())
    monkeypatch.setattr(gate, 'completed_daily_session', lambda *a: pd.Timestamp('2026-09-30').date())
    monkeypatch.setattr(gate, 'coverage_frame', lambda *a: pd.DataFrame([
        dict(ticker=t, event_timestamp=at-pd.Timedelta(minutes=7), ingested_at=at,
             bars=120, invalid_bars=0) for t in tier.symbols]))
    snapshot = gate.run(universe, universe)
    # There must be no market-history rescan on the certified path.
    monkeypatch.setattr(gate, 'coverage_frame', lambda *a: pytest.fail('historical SQL repeated'))
    return at, cp.current_run_id(), snapshot


def test_saved_counts_still_expire_per_symbol_at_actual_publication_time(evidence):
    at, rid, _ = evidence
    gate.validate_publication_freshness(rid, now_utc=at+pd.Timedelta(minutes=3))
    with pytest.raises(RuntimeError, match='PUBLICATION_FRESHNESS_BLOCKED.*CRITICAL_INTRADAY'):
        gate.validate_publication_freshness(rid, now_utc=at+pd.Timedelta(minutes=9))


def test_changed_evidence_cannot_reuse_stage70_hash(evidence):
    at, rid, _ = evidence
    frame = cp.read_dataset('warehouse_coverage')
    frame.at[0, 'evidence'] = []
    cp.write_dataset('warehouse_coverage', frame)
    with pytest.raises(RuntimeError, match='EVIDENCE_HASH'):
        gate.validate_publication_freshness(rid, now_utc=at)


@pytest.mark.parametrize('field,value', [
    ('pg_snapshot', '11:11:'), ('production_run_id', 'another-run'),
    ('symbols_hash', 'different-universe'), ('max_age_minutes', 99),
])
def test_evidence_must_match_boundary_run_catalogue_and_policy(evidence, field, value):
    at, rid, _ = evidence
    frame = cp.read_dataset('warehouse_coverage')
    frame.at[0, field] = value
    cp.write_dataset('warehouse_coverage', frame)
    snapshot = cp.read_dataset('warehouse_snapshot')
    snapshot.at[0, 'coverage_content_hash'] = cp._hash(frame.to_dict('records'))
    cp.write_dataset('warehouse_snapshot', snapshot)
    with pytest.raises(RuntimeError, match='EVIDENCE_PROVENANCE'):
        gate.validate_publication_freshness(rid, now_utc=at)


def test_each_tier_retains_its_own_history_quality_and_missing_requirements(monkeypatch):
    tiers = [gate.CoverageTier('BROAD', ('AAA', 'BBB'), '5m', .5, 20, 10),
             gate.CoverageTier('CRITICAL', ('BBB', 'CCC'), '5m', 1., 120, 10)]
    queries = []
    def read(tier, at, snapshot):
        queries.append((tier.symbols, at, snapshot))
        return pd.DataFrame([dict(ticker=t, bars=30, invalid_bars=0,
            event_timestamp='2026-10-01T14:10Z', ingested_at='2026-10-01T14:17Z')
            for t in ('AAA', 'BBB')])
    monkeypatch.setattr(gate, 'coverage_frame', read)
    at = pd.Timestamp('2026-10-01T14:17Z')
    frames = gate.coverage_frames(tiers, at, '10:10:')
    assert queries == [(('AAA', 'BBB', 'CCC'), at, '10:10:')]
    assert gate.evaluate_tier(tiers[0], frames['BROAD'], now_utc=at)['status'] == 'PASS'
    critical = gate.evaluate_tier(tiers[1], frames['CRITICAL'], now_utc=at)
    assert critical['status'] == 'FAIL'
    assert critical['missing_sample'] == ['CCC']
    assert critical['short_history_sample'] == ['BBB']


@pytest.mark.postgres_integration
def test_evidence_digest_survives_real_jsonb_and_dataframe_roundtrip(monkeypatch):
    import os
    if not os.getenv('DATABASE_URL'):
        pytest.skip('DATABASE_URL required')
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT', raising=False)
    cp.migrate()
    rid = cp.start_run('test-coverage-evidence')
    monkeypatch.setenv('PRODUCTION_RUN_ID', rid)
    setup = evidence.__wrapped__(monkeypatch)
    at, _, _ = setup
    gate.validate_publication_freshness(rid, now_utc=at)
    with pytest.raises(RuntimeError, match='PUBLICATION_FRESHNESS_BLOCKED'):
        gate.validate_publication_freshness(rid, now_utc=at+pd.Timedelta(minutes=9))
