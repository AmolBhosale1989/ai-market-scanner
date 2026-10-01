from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest
import pandas as pd
from scanner import ingestion_fetch as fetch


def requests(count):
    return [([str(i)], [], '2d', '5d', '5m') for i in range(count)]


def test_bounded_ordered_prefetch_and_join(monkeypatch):
    lock=threading.Lock()
    state={'active':0,'max_active':0,'submitted':0,'consumed':0,'max_pending':0}
    def job(value):
        value = int(value[0])
        with lock:
            state['active']+=1
            state['max_active']=max(state['max_active'],state['active'])
        time.sleep(.005 * (3-value%3))
        with lock:
            state['active']-=1
        return pd.DataFrame({'value': [value]})
    class Executor(ThreadPoolExecutor):
        def __init__(self, **kwargs):
            super().__init__(max_workers=kwargs['max_workers'])
        def submit(self,*args):
            state['submitted']+=1
            state['max_pending']=max(state['max_pending'],state['submitted']-state['consumed'])
            return super().submit(*args)
    monkeypatch.setattr(fetch,'fetch_symbol',job)
    result=[]
    for value in fetch.ordered_prefetch(requests(20),workers=2,executor_factory=Executor):
        result.append(next(iter(value.values())).iloc[0]['value'])
        state['consumed']+=1
    assert result==list(range(20))
    assert state['max_active']==2
    assert state['max_pending']<=4
    assert state['active']==0


def test_worker_error_propagates_and_joins(monkeypatch):
    completed=[]
    def job(value):
        value = int(value[0])
        if value==0:
            raise RuntimeError('provider failed')
        time.sleep(.01)
        completed.append(value)
        return pd.DataFrame({'value': [value]})
        return value
    def executor_factory(**kwargs):
        return ThreadPoolExecutor(max_workers=kwargs['max_workers'])
    monkeypatch.setattr(fetch,'fetch_symbol',job)
    with pytest.raises(RuntimeError,match='provider failed'):
        list(fetch.ordered_prefetch(requests(10),workers=2,executor_factory=executor_factory))
    after=list(completed)
    time.sleep(.02)
    assert completed==after
    assert len(completed)<=3


def test_rolling_symbols_keep_slots_busy_and_cap_total_workers(monkeypatch):
    lock = threading.Lock()
    state = {'active': 0, 'maximum': 0}
    completed = []
    def job(request):
        symbol, period, interval = request
        with lock:
            state['active'] += 1
            state['maximum'] = max(state['maximum'], state['active'])
        time.sleep(.06 if symbol == '0' else .002)
        with lock:
            state['active'] -= 1
            completed.append(symbol)
        return pd.DataFrame({'symbol': [symbol], 'period': [period]})
    monkeypatch.setattr(fetch, 'fetch_symbol', job)
    chunks = [(list(map(str, range(20))), ['NEW'], '2d', '5d', '5m'),
              (['NEXT'], [], '2d', '5d', '5m')]
    result = list(fetch.ordered_prefetch(chunks, workers=100))
    assert 1 < state['maximum'] <= 10
    assert completed.index('10') < completed.index('0')
    assert result[0]['NEW'].iloc[0]['period'] == '5d'
    assert result[1]['NEXT'].iloc[0]['period'] == '2d'
    assert set(result[0]) == set(map(str, range(20))) | {'NEW'}
    assert state['active'] == 0


def test_symbol_http_timeouts_retry_limit_and_rate_limit(monkeypatch, capsys):
    from scanner import data
    calls = []
    class Client:
        def history(self, **kwargs):
            calls.append(kwargs)
            raise TimeoutError('provider read timed out')
    monkeypatch.setattr(data.yf, 'Ticker', lambda _: Client())
    assert data.download_symbol('AAA', period='2d', interval='5m', max_attempts=99).empty
    assert len(calls) == 2
    assert all(0 < call['timeout'] <= 8 for call in calls)
    output = capsys.readouterr().out
    assert 'http_timeout' in output and '"retry_count": 1' in output
    def limited(self, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('HTTP 429 rate limit')
    monkeypatch.setattr(Client, 'history', limited)
    calls.clear()
    assert data.download_symbol('AAA', period='2d', interval='5m').empty
    assert len(calls) == 1
    assert 'rate_limit' in capsys.readouterr().out
