import json
import time
import pytest
from scanner.execution_timing import profiled, phase
from scanner.ingestion_deadline import run_bounded


def sleepy():
    time.sleep(20)


def noop():
    pass


def test_profile_exclusive_and_errors(capsys):
    @profiled('test')
    def run():
        with phase('database_read'):
            time.sleep(.01)
            with phase('result_mapping'):
                time.sleep(.01)
        raise ValueError('secret')
    with pytest.raises(ValueError): run()
    output=capsys.readouterr().out
    record=json.loads(output.split('EXECUTION_MICRO_TIMING ')[1])
    assert record['status']=='FAIL'
    assert record['phases']['database_read']['wall_seconds']>=.009
    assert record['phases']['result_mapping']['wall_seconds']>=.009
    assert 'secret' not in output


def test_ingestion_process_deadline():
    start=time.monotonic()
    with pytest.raises(RuntimeError,match='INGESTION_OVERALL_DEADLINE'):
        run_bounded(sleepy,seconds=.5)
    assert time.monotonic()-start<5
    run_bounded(noop,seconds=5)


def test_v4_profile_includes_initialization_and_failed_metric(monkeypatch, capsys):
    from types import SimpleNamespace
    from scanner import v4_worker

    def build(_args):
        with phase('database_read'):
            time.sleep(.01)
        def cycle():
            with phase('result_write'):
                time.sleep(.01)
            return SimpleNamespace(success=False)
        return SimpleNamespace(run_cycle=cycle)

    monkeypatch.setattr(v4_worker, 'build_worker', build)
    with pytest.raises(SystemExit) as error:
        v4_worker.run_once(SimpleNamespace())
    assert error.value.code == 1
    line = next(x for x in capsys.readouterr().out.splitlines()
                if x.startswith('EXECUTION_MICRO_TIMING '))
    record = json.loads(line.split(' ', 1)[1])
    assert record['block'] == 'v4_live' and record['status'] == 'FAIL'
    assert record['phases']['database_read']['wall_seconds'] >= .009
    assert record['phases']['result_write']['wall_seconds'] >= .009
    assert all(v['process_cpu_seconds'] >= 0 for v in record['phases'].values())


def test_profile_measures_worker_cpu_separately_from_wait(monkeypatch, capsys):
    from scanner import execution_timing as timing
    # Controlled clocks prove worker process CPU is not confused with caller CPU.
    wall = iter([0., 1., 4., 5.])
    thread = iter([0., .1, .2, .3])
    process = iter([0., .1, 2.1, 2.2])
    monkeypatch.setattr(timing.time, 'perf_counter', lambda: next(wall))
    monkeypatch.setattr(timing.time, 'thread_time', lambda: next(thread))
    monkeypatch.setattr(timing.time, 'process_time', lambda: next(process))
    @profiled('parallel-test')
    def run():
        with phase('parallel_calculation'):
            pass
    run()
    record = json.loads(capsys.readouterr().out.split('EXECUTION_MICRO_TIMING ')[1])
    interval = record['phases']['parallel_calculation']
    assert interval['wall_seconds'] == 3.
    assert interval['cpu_seconds'] == pytest.approx(.1)
    assert interval['process_cpu_seconds'] == pytest.approx(2.)
