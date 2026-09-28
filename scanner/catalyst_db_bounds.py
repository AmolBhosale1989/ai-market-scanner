"""Alpha persistence bounds. Never reuse a connection after deadline cancellation."""
from contextlib import contextmanager
import threading
import time

from .database import connection


class CatalystDBTimeout(RuntimeError):
    pass


@contextmanager
def bounded_persistence(*, deadline_seconds=120, acquire_seconds=5,
                        lock_ms=3000, statement_ms=15000):
    if min(deadline_seconds, acquire_seconds, lock_ms, statement_ms) <= 0:
        raise ValueError('Persistence bounds must be positive')
    started = time.monotonic()
    expired = threading.Event()
    timer = None
    phase = 'connection'
    try:
        with connection(timeout=min(acquire_seconds, deadline_seconds)) as conn:
            remaining = deadline_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise CatalystDBTimeout('OVERALL_DEADLINE')

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
                phase = 'statement'
                with conn.cursor() as cur:
                    cur.execute("SELECT set_config('lock_timeout',%s,true), "
                                "set_config('statement_timeout',%s,true)",
                                (f'{lock_ms}ms', f'{statement_ms}ms'))
                    yield cur
                    if expired.is_set() or time.monotonic() - started >= deadline_seconds:
                        raise CatalystDBTimeout('OVERALL_DEADLINE')
                phase = 'commit'
                conn.commit()
                if expired.is_set():
                    raise CatalystDBTimeout('OVERALL_DEADLINE')
            except BaseException:
                # Disconnect aborts uncommitted work without an unbounded
                # ROLLBACK round trip on an unhealthy connection.
                conn.close()
                raise
            finally:
                timer.cancel()
                timer.join()
            if expired.is_set():
                raise CatalystDBTimeout('OVERALL_DEADLINE')
            # No watchdog may outlive checkout and cancel another borrower's SQL.
    except Exception as exc:
        code = getattr(exc, 'sqlstate', None)
        if expired.is_set() or isinstance(exc, CatalystDBTimeout):
            kind = 'OVERALL_DEADLINE'
        elif phase == 'connection':
            kind = 'DB_CONNECTION_TIMEOUT' if type(exc).__name__ == 'PoolTimeout' else 'DB_CONNECTION_ERROR'
        elif code == '55P03':
            kind = 'DB_LOCK_TIMEOUT'
        elif code == '57014':
            kind = 'DB_STATEMENT_TIMEOUT' if 'statement timeout' in str(exc) else 'DB_QUERY_CANCELLED'
        elif code == '40P01':
            kind = 'DB_DEADLOCK'
        else:
            kind = 'DB_ERROR'
        outcome = 'COMMIT_UNCERTAIN' if phase == 'commit' else 'TRANSACTION_ABORTED'
        print(f'CATALYST_PERSIST_FAILURE provider=ALPHA_VANTAGE kind={kind} '
              f'phase={phase} outcome={outcome} sqlstate={code} '
              f'error={type(exc).__name__} elapsed_seconds={time.monotonic()-started:.3f}', flush=True)
        raise CatalystDBTimeout(kind) from None
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
