"""Exclusive wall/thread-CPU attribution; no payload or SQL text in logs."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import time

_active = ContextVar('execution_profile', default=None)


class Profile:
    def __init__(self, block):
        self.block, self.values = block, {}
        self.category = 'calculation'
        self.wall, self.cpu = time.perf_counter(), time.thread_time()
    def charge(self):
        wall, cpu = time.perf_counter(), time.thread_time()
        value = self.values.setdefault(self.category, [0., 0.])
        value[0] += wall-self.wall
        value[1] += cpu-self.cpu
        self.wall, self.cpu = wall, cpu


@contextmanager
def phase(name):
    profile = _active.get()
    if profile is None:
        yield
        return
    profile.charge()
    previous, profile.category = profile.category, name
    try:
        yield
    finally:
        profile.charge()
        profile.category = previous


def timed(name):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with phase(name):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def profiled(block):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            profile=Profile(block)
            token=_active.set(profile)
            status='PASS'
            try:
                return fn(*args, **kwargs)
            except BaseException:
                status='FAIL'
                raise
            finally:
                profile.charge()
                _active.reset(token)
                print('EXECUTION_MICRO_TIMING '+json.dumps(dict(block=block,status=status,
                    phases={k:dict(wall_seconds=v[0],cpu_seconds=v[1]) for k,v in profile.values.items()})),flush=True)
        return wrapped
    return decorate
