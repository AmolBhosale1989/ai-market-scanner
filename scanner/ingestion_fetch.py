"""Rolling, bounded intraday fetches; only the caller normalizes and writes."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


MAX_FETCH_WORKERS = 10


def fetch_symbol(request):
    from .data import download_symbol
    symbol, period, interval = request
    return download_symbol(symbol, period=period, interval=interval)


def ordered_prefetch(requests, *, workers=10, executor_factory=ThreadPoolExecutor):
    """Bound HTTP callers and lookahead while retaining deterministic DB order.

    Each task owns a Ticker.history call; no nested yf.download batch executor.
    A completed symbol immediately frees a slot without a provider-batch barrier.
    """
    workers = max(1, min(MAX_FETCH_WORKERS, int(workers)))
    requests = list(requests)
    tasks, expected = [], []
    for index, (existing, missing, incremental, period, interval) in enumerate(requests):
        symbols = [(symbol, incremental, interval) for symbol in existing]
        symbols += [(symbol, period, interval) for symbol in missing]
        expected.append(len(symbols))
        tasks.extend((index, request) for request in symbols)
    ready = [{} for _ in requests]
    received = [0 for _ in requests]
    next_task = next_chunk = 0
    with executor_factory(max_workers=workers) as executor:
        pending = {}
        try:
            while next_chunk < len(requests):
                while next_chunk < len(requests) and received[next_chunk] == expected[next_chunk]:
                    yield ready[next_chunk]
                    ready[next_chunk] = None
                    next_chunk += 1
                while next_task < len(tasks) and len(pending) < 2 * workers:
                    index, request = tasks[next_task]
                    if index >= next_chunk + max(2, workers):
                        break
                    pending[executor.submit(fetch_symbol, request)] = (index, request[0])
                    next_task += 1
                if not pending:
                    continue
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    index, symbol = pending.pop(future)
                    frame = future.result()
                    received[index] += 1
                    if frame is not None and not frame.empty:
                        ready[index][symbol] = frame
        finally:
            for future in pending:
                future.cancel()
