from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest
from scanner import ingestion_fetch as fetch


def test_bounded_ordered_prefetch_and_join(monkeypatch):
    lock=threading.Lock()
    state={'active':0,'max_active':0,'submitted':0,'consumed':0,'max_pending':0}
    def job(value):
        with lock:
            state['active']+=1
            state['max_active']=max(state['max_active'],state['active'])
        time.sleep(.005 * (3-value%3))
        with lock:
            state['active']-=1
        return value
    class Executor(ThreadPoolExecutor):
        def __init__(self, **kwargs):
            super().__init__(max_workers=kwargs['max_workers'])
        def submit(self,*args):
            state['submitted']+=1
            state['max_pending']=max(state['max_pending'],state['submitted']-state['consumed'])
            return super().submit(*args)
    monkeypatch.setattr(fetch,'fetch_chunk',job)
    result=[]
    for value in fetch.ordered_prefetch(range(20),workers=2,executor_factory=Executor):
        result.append(value)
        state['consumed']+=1
    assert result==list(range(20))
    assert state['max_active']==2
    assert state['max_pending']<=4
    assert state['active']==0


def test_worker_error_propagates_and_joins(monkeypatch):
    completed=[]
    def job(value):
        if value==0:
            raise RuntimeError('provider failed')
        time.sleep(.01)
        completed.append(value)
        return value
    def executor_factory(**kwargs):
        return ThreadPoolExecutor(max_workers=kwargs['max_workers'])
    monkeypatch.setattr(fetch,'fetch_chunk',job)
    with pytest.raises(RuntimeError,match='provider failed'):
        list(fetch.ordered_prefetch(range(10),executor_factory=executor_factory))
    after=list(completed)
    time.sleep(.02)
    assert completed==after
    assert len(completed)<=3
