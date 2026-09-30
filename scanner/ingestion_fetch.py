"""Bounded, ordered provider prefetch; worker processes never acquire DB handles."""
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
import os


def initialize_worker():
    # Isolate yfinance's process-global thread settings and cap total fan-out.
    os.environ['YAHOO_DOWNLOAD_THREADS'] = '5'


def fetch_chunk(request):
    from .data import download_batch
    existing, missing, incremental_period, period, interval = request
    batch = {}
    if existing:
        batch.update(download_batch(existing, period=incremental_period, interval=interval))
    if missing:
        batch.update(download_batch(missing, period=period, interval=interval))
    return batch


def ordered_prefetch(requests, *, workers=2, executor_factory=ProcessPoolExecutor):
    """At most 2*workers queued results; drain workers before returning/errors."""
    workers = max(1, min(2, workers))
    source = iter(requests)
    with executor_factory(max_workers=workers, mp_context=get_context('spawn'),
                          initializer=initialize_worker) as executor:
        pending = deque()
        try:
            for _ in range(2 * workers):
                request = next(source, None)
                if request is None:
                    break
                pending.append(executor.submit(fetch_chunk, request))
            while pending:
                result = pending.popleft().result()
                yield result
                request = next(source, None)
                if request is not None:
                    pending.append(executor.submit(fetch_chunk, request))
        finally:
            for future in pending:
                future.cancel()
