import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from scanner.ingestion_timing import BatchTiming


@pytest.mark.parametrize('failure', [None, 'body', 'commit', 'acquire'])
def test_transaction_outcome_and_timing(failure, capsys):
    events = []
    def commit():
        events.append('commit')
        if failure == 'commit':
            raise ConnectionError('secret must not be logged')
    @contextmanager
    def factory():
        if failure == 'acquire':
            raise TimeoutError('secret must not be logged')
        try:
            yield SimpleNamespace(commit=commit)
        except BaseException:
            events.append('rollback')
            raise
        finally:
            events.append('release')
    def execute():
        with BatchTiming(run_id='test') as timing:
            with timing.connection(factory):
                with timing.phase('insert_execution'):
                    if failure == 'body':
                        raise ValueError('secret must not be logged')
    if failure:
        with pytest.raises((ValueError, ConnectionError, TimeoutError)):
            execute()
    else:
        execute()
    output = capsys.readouterr().out
    assert 'secret' not in output
    data = json.loads(output.split('WAREHOUSE_MICRO_TIMING ')[1])
    assert data['commit_state'] == {None:'committed', 'body':'rolled_back',
                                    'commit':'uncertain', 'acquire':'not_attempted'}[failure]
    assert ('commit' in events) == (failure in (None, 'commit'))
    assert data['status'] == ('error' if failure else 'success')
    for phase in data['phases'].values():
        assert phase['wall_ms'] >= 0 and phase['cpu_ms'] >= 0
