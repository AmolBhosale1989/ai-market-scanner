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
