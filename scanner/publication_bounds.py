"""Publication transaction bounds. Never reuse a connection after deadline cancellation."""
from contextlib import contextmanager
import threading
import time

from .database import connection
from .execution_timing import phase as measure


class PublicationDBTimeout(RuntimeError):
    pass


@contextmanager
def bounded_publication(*, deadline_seconds=90, acquire_seconds=5,
                        lock_ms=3000, statement_ms=15000):
    if min(deadline_seconds, acquire_seconds, lock_ms, statement_ms) <= 0:
        raise ValueError('Persistence bounds must be positive')
    started = time.monotonic()
    expired = threading.Event()
    timer = None
    monitor = None
    operation = 'connection'
    try:
        with measure('connection_acquisition'):
            checkout = connection(timeout=min(acquire_seconds, deadline_seconds))
            conn = checkout.__enter__()
        try:
            remaining = deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise PublicationDBTimeout('OVERALL_DEADLINE')

            def expire():
                expired.set()
                # Bound cancellation itself, then discard the socket. Closing an
                # uncommitted session makes PostgreSQL roll back the transaction.
                try:
                    conn.cancel_safe(timeout=2)
                except Exception:
                    pass
                finally:
                    conn.close()

            timer = threading.Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            try:
                operation = 'statement'
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('lock_timeout',%s,true), "
                                "set_config('statement_timeout',%s,true)",
                                (f'{lock_ms}ms', f'{statement_ms}ms'))
                    print(f'PUBLICATION_BACKEND pid={conn.info.backend_pid}',flush=True)
                    from .publication_lock_watch import watch
                    monitor=watch(conn.info.backend_pid)
                    monitor.__enter__()
                    yield cur
                    if expired.is_set() or time.monotonic() - started >= deadline_seconds:
                        raise PublicationDBTimeout('OVERALL_DEADLINE')
                operation = 'commit'
                with measure('transaction_commit'):
                    conn.commit()
                if expired.is_set():
                    raise PublicationDBTimeout('OVERALL_DEADLINE')
            except BaseException:
                # Disconnect aborts uncommitted work without an unbounded
                # ROLLBACK round trip on an unhealthy connection.
                conn.close()
                raise
            finally:
                timer.cancel()
                timer.join()
            if expired.is_set():
                raise PublicationDBTimeout('OVERALL_DEADLINE')
            # No watchdog may outlive checkout and cancel another borrower's SQL.
        except BaseException as exc:
            conn.close()
            checkout.__exit__(type(exc),exc,exc.__traceback__)
            raise
        else:
            checkout.__exit__(None,None,None)
    except Exception as exc:
        code = getattr(exc, 'sqlstate', None)
        if expired.is_set() or isinstance(exc, PublicationDBTimeout):
            kind = 'OVERALL_DEADLINE'
        elif operation == 'connection':
            kind = 'DB_CONNECTION_TIMEOUT' if type(exc).__name__ == 'PoolTimeout' else 'DB_CONNECTION_ERROR'
        elif code == '55P03':
            kind = 'DB_LOCK_TIMEOUT'
        elif code == '57014':
            kind = 'DB_STATEMENT_TIMEOUT' if 'statement timeout' in str(exc) else 'DB_QUERY_CANCELLED'
        elif code == '40P01':
            kind = 'DB_DEADLOCK'
        else:
            kind = 'DB_ERROR'
        outcome = 'COMMIT_UNCERTAIN' if operation == 'commit' else 'TRANSACTION_ABORTED'
        print(f'PUBLICATION_TRANSACTION_FAILURE kind={kind} '
              f'phase={operation} outcome={outcome} sqlstate={code} '
              f'error={type(exc).__name__} elapsed_seconds={time.monotonic()-started:.3f}', flush=True)
        if kind == 'DB_ERROR' and not code:
            raise
        raise PublicationDBTimeout(kind) from None
    finally:
        if monitor is not None:
            with measure('lock_monitor_shutdown'):
                monitor.__exit__(None,None,None)
        if timer is not None:
            timer.cancel()
            timer.join()


class TimedCursor:
    """Number SQL round trips without exposing statement text or parameters."""
    def __init__(self, cursor):
        self.cursor, self.sequence = cursor, 0
    def execute(self, *args, **kwargs):
        self.sequence += 1
        with measure(f'sql_{self.sequence:02d}'):
            return self.cursor.execute(*args, **kwargs)
    def executemany(self, *args, **kwargs):
        self.sequence += 1
        with measure(f'sql_{self.sequence:02d}_batch'):
            return self.cursor.executemany(*args, **kwargs)
    def fetchall(self):
        with measure('payload_decode'):
            return self.cursor.fetchall()
    def fetchone(self):
        with measure('row_decode'):
            return self.cursor.fetchone()
    def __getattr__(self, name):
        return getattr(self.cursor, name)
