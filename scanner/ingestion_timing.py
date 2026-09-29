"""Per-batch diagnostic wall/CPU timing; never logs payloads or credentials."""
from contextlib import contextmanager
import json
from time import perf_counter_ns, thread_time_ns


class BatchTiming:
    def __init__(self, **metadata):
        self.metadata = metadata
        self.phases = {}
        self.inserted = None
        self.commit_state = 'not_attempted'

    def __enter__(self):
        self.started = perf_counter_ns()
        return self

    @contextmanager
    def phase(self, name):
        wall, cpu = perf_counter_ns(), thread_time_ns()
        status = 'success'
        try:
            yield
        except BaseException:
            status = 'error'
            raise
        finally:
            self.phases[name] = dict(wall_ms=(perf_counter_ns()-wall)/1e6,
                                     cpu_ms=(thread_time_ns()-cpu)/1e6, status=status)

    @contextmanager
    def connection(self, factory):
        # Preserve the pool context's rollback/release behavior on all exceptions.
        with self.phase('connection_acquisition'):
            context = factory()
            conn = context.__enter__()
        try:
            yield conn
            self.commit_state = 'uncertain'
            with self.phase('transaction_commit'):
                conn.commit()
            self.commit_state = 'committed'
        except BaseException as exc:
            with self.phase('rollback_and_release'):
                context.__exit__(type(exc), exc, exc.__traceback__)
            if self.commit_state == 'not_attempted':
                self.commit_state = 'rolled_back'
            raise
        else:
            with self.phase('pool_release'):
                context.__exit__(None, None, None)

    def __exit__(self, exc_type, exc, tb):
        print('WAREHOUSE_MICRO_TIMING ' + json.dumps({
            **self.metadata, 'status': 'error' if exc_type else 'success',
            'error_type': exc_type.__name__ if exc_type else None,
            'commit_state': self.commit_state, 'inserted_rows': self.inserted,
            'total_wall_ms': (perf_counter_ns()-self.started)/1e6,
            'phases': self.phases,
        }, sort_keys=True), flush=True)
        return False
