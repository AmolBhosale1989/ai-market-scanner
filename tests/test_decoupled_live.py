from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import uuid

import pandas as pd
import pytest

from scanner import control_plane as cp, feeder_handoff as handoff, live_session
from scanner.consumer_snapshot import capture_boundary
from scanner.stage_contract import ENGINE_DEPENDENCIES, validate_stages


def test_session_skips_ticks_without_overlap_or_catchup():
    clock = [0.0]
    starts = []
    def execute(command, check):
        starts.append(clock[0])
        assert command[-1] == 'engine' and check
        clock[0] += [70, 250, 65][len(starts)-1]
    def sleep(seconds):
        clock[0] += seconds
    count = live_session.run_session('engine', max_cycles=3, clock=lambda: clock[0],
        sleep=sleep, execute=execute, session_check=lambda **_: (True, 'open'))
    assert count == 3
    assert starts == [0, 120, 370]  # overrun starts immediately, without overlap


def test_session_failure_does_not_start_another_cycle():
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args[0])
    with pytest.raises(subprocess.CalledProcessError):
        live_session.run_session('feeder', max_cycles=2, execute=fail,
                                 session_check=lambda **_: (True, 'open'))
    assert len(calls) == 1


def test_session_cancellation_terminates_the_cycle_group(monkeypatch):
    import signal
    events = []
    class Child:
        pid = 12345
        calls = 0
        def wait(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt()
            events.append(('reaped', kwargs))
            return -15
    monkeypatch.setattr(live_session.subprocess, 'Popen', lambda *a, **k: Child())
    monkeypatch.setattr(live_session.os, 'killpg', lambda pid, sig: events.append((pid, sig)))
    with pytest.raises(KeyboardInterrupt):
        live_session.execute_cycle(['timeout', '600s', 'example'], check=True)
    assert events == [(12345, signal.SIGTERM), ('reaped', {'timeout': 5})]


def test_engine_failure_terminates_parallel_strategy_processes(monkeypatch):
    import signal
    from scanner import live_cycle
    killed = []
    class Child:
        pid = 12345
        args = ['bash', 'scripts/live_engine.sh']
        def wait(self):
            return 7
    monkeypatch.setattr(live_cycle.subprocess, 'Popen', lambda *a, **k: Child())
    monkeypatch.setattr(live_cycle.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(subprocess.CalledProcessError):
        live_cycle.run_engine()
    assert killed == [(12345, signal.SIGKILL)]


def test_closed_session_never_starts_a_cycle():
    assert live_session.run_session('engine',
        execute=lambda *a, **k: pytest.fail('off-hours cycle'),
        session_check=lambda **_: (False, 'market_closed')) == 0


@pytest.mark.parametrize('cycles', [0, -1, 61])
def test_invalid_cycle_budget_is_rejected(cycles):
    with pytest.raises(ValueError):
        live_session.run_session('engine', max_cycles=cycles)


def test_session_does_not_start_past_its_deadline():
    clock = [0.0]
    calls = []
    def execute(*a, **k):
        calls.append(1)
        clock[0] += 601
    assert live_session.run_session('engine', minutes=11, max_cycles=2,
        clock=lambda: clock[0], execute=execute,
        session_check=lambda **_: (True, 'open')) == 1


def test_engine_dag_has_no_fake_ingestion_and_retains_join():
    script = Path('scripts/live_engine.sh').read_text()
    names = set(re.findall(r'^stage ([a-z0-9_]+) \d+ ', script, re.M))
    assert names | {'engine_seed'} == set(ENGINE_DEPENDENCIES)
    assert '--require-feeder' in script
    assert 'scanner.live_ingestion' not in script
    assert '--ingest-only' not in script
    assert script.index('stage warehouse_gate') < script.index('export WAREHOUSE_CONSUMER_SNAPSHOT=1')
    start = datetime(2026, 10, 1, 15, tzinfo=timezone.utc)
    rows = {}
    for name, parents in ENGINE_DEPENDENCIES.items():
        begin = max((rows[p]['completed_at'] for p in parents), default=start)
        rows[name] = dict(stage_name=name, status='PASS', started_at=begin,
                          completed_at=begin + timedelta(seconds=1))
    assert validate_stages(list(rows.values()), run_started_at=start,
                           now=start+timedelta(minutes=3)) == 'engine'
    rows['strategy_finalize']['started_at'] = start
    with pytest.raises(RuntimeError, match='ACCEPTANCE_DEPENDENCY'):
        validate_stages(list(rows.values()), run_started_at=start,
                        now=start+timedelta(minutes=3))


@pytest.mark.parametrize('fail_at', ['', 'warehouse_gate', 'premarket', 'v4_live'])
def test_engine_shell_cannot_publish_after_a_failed_dependency(fail_at):
    script = Path('scripts/live_engine.sh').read_text()
    shim = '''
python() {
  printf '%s\\n' "$*"
  if [[ -n "$FAIL_AT" && "$*" == "-m scanner.${FAIL_AT}"* ]]; then return 7; fi
  return 0
}
bash() { printf '%s\\n' "$*"; }
'''
    # V4's executable is v4_worker, while the executed stage is v4_live.
    failure = 'v4_worker' if fail_at == 'v4_live' else fail_at
    result = subprocess.run(['bash'], input=f'FAIL_AT={failure}\n'+shim+script,
                            text=True, capture_output=True)
    assert (result.returncode == 0) == (not fail_at), result.stderr
    assert ('--finalize --mode production' in result.stdout) == (not fail_at)


def test_cutover_is_opt_in_and_publisher_mutex_is_shared():
    feeder = Path('.github/workflows/live_feeder.yml').read_text()
    engine = Path('.github/workflows/live_engine.yml').read_text()
    production = Path('.github/workflows/production.yml').read_text()
    assert "vars.LIVE_PIPELINES_ENABLED == 'true'" in feeder
    assert "vars.LIVE_PIPELINES_ENABLED == 'true'" in engine
    assert "vars.LIVE_PIPELINES_ENABLED != 'true'" in production
    assert 'group: market-hunt-production' in engine and 'group: market-hunt-production' in production
    assert 'group: market-hunt-live-feeder' in feeder
    for text in (feeder, engine):
        assert 'cancel-in-progress: false' in text
        assert 'default: 1' in text
        assert 'actions: write' not in text  # no uncontrolled continuation dispatch
        assert 'format(\'{0}\', inputs.cycles)' in text  # zero stays zero and is rejected


def test_supervised_feeder_dispatch_cannot_execute_the_publisher():
    production = Path('.github/workflows/production.yml').read_text()
    feeder = Path('.github/workflows/live_feeder.yml').read_text()
    jobs = production.split('\njobs:\n', 1)[1]
    assert re.findall(r'^  ([a-z_]+):$', jobs, re.M) == ['feeder_validation', 'publish']
    validation, publisher = jobs.split('\n  publish:\n', 1)
    assert "if: ${{ github.event_name == 'workflow_dispatch' && inputs.mode == 'feeder' }}" in validation
    assert 'uses: ./.github/workflows/live_feeder.yml' in validation
    assert 'with:\n      cycles: 1\n' in validation
    assert "if: ${{ inputs.mode != 'feeder' && (" in publisher
    assert '  workflow_call:\n' in feeder
    assert 'scanner.live_session feeder' in feeder
    assert 'options: [full, live, feeder]' in production


@pytest.fixture
def db_handoff(monkeypatch):
    if not os.getenv('DATABASE_URL'):
        pytest.skip('isolated PostgreSQL required')
    cp.migrate()
    commit = 'test-' + uuid.uuid4().hex
    def create(role, *, symbols=('AAA',), source=None):
        rid = cp.start_run('feeder' if role == 'feeder' else 'production',
                           source_commit=source or commit)
        for name in handoff.CATALOGUE_DATASETS:
            cp.write_dataset(name, pd.DataFrame({'ticker': list(symbols)}), run_id=rid)
        with cp._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE pipeline_run SET metadata=%s::jsonb WHERE pipeline_run_id=%s",
                        (cp._canonical({'lane': role}), rid))
        return rid
    def complete(rid):
        for i, name in enumerate(handoff.FEEDER_STAGES):
            cp.stage_started(name, i, run_id=rid)
            cp.stage_finished(name, run_id=rid)
        return handoff.complete_feeder(rid)
    return create, complete


@pytest.mark.postgres_integration
def test_engine_binds_committed_matching_feeder_and_same_snapshot(db_handoff):
    create, complete = db_handoff
    feeder, engine = create('feeder'), create('engine')
    complete(feeder)
    at, snapshot = capture_boundary(run_id=engine, require_feeder=True)
    cp.write_dataset('warehouse_snapshot', pd.DataFrame([dict(status='PASS',
        production_run_id=engine, as_of_utc=at.isoformat(), pg_snapshot=snapshot)]),
        entity_key=None, run_id=engine)
    with cp._connect() as conn, conn.cursor() as cur:
        handoff.validate_binding(cur, engine)
        cur.execute('SELECT feeder_run_id::text FROM engine_feeder_binding WHERE pipeline_run_id=%s', (engine,))
        assert cur.fetchone()[0] == feeder
        cur.execute('SELECT count(*) FROM publication_snapshot WHERE pipeline_run_id=%s', (feeder,))
        assert cur.fetchone()[0] == 0


@pytest.mark.postgres_integration
@pytest.mark.parametrize('mismatch', ['missing', 'universe', 'commit'])
def test_missing_or_mismatched_feeder_fails_before_any_anchor_is_saved(db_handoff, mismatch):
    create, complete = db_handoff
    if mismatch != 'missing':
        feeder = create('feeder', symbols=('OTHER',) if mismatch == 'universe' else ('AAA',),
                        source='different-head' if mismatch == 'commit' else None)
        complete(feeder)
    engine = create('engine')
    with pytest.raises(RuntimeError, match='ENGINE_FEEDER_NOT_READY'):
        capture_boundary(run_id=engine, require_feeder=True)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM frozen_catalogue WHERE pipeline_run_id=%s', (engine,))
        assert cur.fetchone()[0] == 0  # anchor/catalogue and binding roll back together


@pytest.mark.postgres_integration
def test_incomplete_feeder_never_creates_receipt(db_handoff):
    create, _ = db_handoff
    feeder = create('feeder')
    cp.stage_started('seed_plan', 10, run_id=feeder)
    with pytest.raises(RuntimeError, match='FEEDER_STAGE_SET'):
        handoff.complete_feeder(feeder)
    with cp._connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM feeder_receipt WHERE pipeline_run_id=%s', (feeder,))
        assert cur.fetchone()[0] == 0


@pytest.mark.postgres_integration
def test_late_committing_receipt_stays_invisible_at_original_snapshot(db_handoff):
    create, _ = db_handoff
    feeder, engine = create('feeder'), create('engine')
    # Its timestamp predates T0, but the xid is still inflight at snapshot capture.
    with cp._connect() as held, held.cursor() as cur:
        hashes = handoff._catalogue_hashes(cur, feeder)
        cur.execute('SELECT clock_timestamp()')
        completed = cur.fetchone()[0]
        payload = {'feeder_run_id': feeder, 'completed_at': completed.isoformat()}
        cur.execute("""INSERT INTO feeder_receipt
            (pipeline_run_id,completed_at,source_commit,catalogue_hashes,content_hash,payload)
            SELECT pipeline_run_id,%s,source_commit,%s::jsonb,%s,%s::jsonb
            FROM pipeline_run WHERE pipeline_run_id=%s""",
            (completed, cp._canonical(hashes), cp._hash(payload), cp._canonical(payload), feeder))
        cur.execute("UPDATE pipeline_run SET status='INGESTED' WHERE pipeline_run_id=%s", (feeder,))
        at, visibility = capture_boundary(run_id=engine)
    # held committed now. A replay with the OLD snapshot must still reject it.
    with cp._connect() as conn, conn.cursor() as cur:
        with pytest.raises(RuntimeError, match='ENGINE_FEEDER_NOT_READY'):
            handoff.bind_at_snapshot(cur, engine, at, visibility)


@pytest.mark.postgres_integration
def test_receipt_is_immutable_and_cannot_publish(db_handoff):
    create, complete = db_handoff
    feeder = create('feeder')
    complete(feeder)
    with pytest.raises(Exception, match='LIVE_HANDOFF_IMMUTABLE'):
        with cp._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE feeder_receipt SET content_hash='changed' WHERE pipeline_run_id=%s", (feeder,))
    with pytest.raises(RuntimeError, match='CONTROL_PLANE_RUN_NOT_PUBLISHABLE'):
        cp.publish('test-feeder', ['live_universe'], run_id=feeder)


@pytest.mark.postgres_integration
def test_changed_engine_snapshot_rejects_binding(db_handoff):
    create, complete = db_handoff
    feeder, engine = create('feeder'), create('engine')
    complete(feeder)
    at, visibility = capture_boundary(run_id=engine, require_feeder=True)
    cp.write_dataset('warehouse_snapshot', pd.DataFrame([dict(production_run_id=engine,
        as_of_utc=(at+pd.Timedelta(seconds=1)).isoformat(), pg_snapshot=visibility)]),
        entity_key=None, run_id=engine)
    with cp._connect() as conn, conn.cursor() as cur:
        with pytest.raises(RuntimeError, match='ENGINE_FEEDER_BINDING_INVALID'):
            handoff.validate_binding(cur, engine)
