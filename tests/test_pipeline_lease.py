import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from scanner.pipeline_lease import acquire, heartbeat, finish, _ttl
from scanner.bitemporal_warehouse import _connect
from scanner.control_plane import migrate


@pytest.mark.parametrize('value', [0, -1, 301, True, 2.5])
def test_reject_unbounded_ttl(value):
    with pytest.raises(ValueError):
        _ttl(value)


@pytest.mark.postgres_integration
def test_atomic_takeover_fences_old_owner_and_preserves_history():
    if not os.getenv('DATABASE_URL'):
        pytest.skip('Requires isolated PostgreSQL test database')
    migrate()
    scope = 'lease-test-' + str(uuid.uuid4())
    def claim(_):
        return acquire(scope, str(uuid.uuid4()), date(2026, 9, 25))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    winners = [x for x in claims if x is not None]
    assert len(winners) == 1
    first = winners[0]
    heartbeat(first)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE pipeline_lease SET expires_at=clock_timestamp()-interval '1 second' WHERE scope=%s", (scope,))
    with pytest.raises(RuntimeError, match='PIPELINE_LEASE_LOST'):
        heartbeat(first)
    second = claim(None)
    assert second.generation == first.generation + 1
    with pytest.raises(RuntimeError, match='PIPELINE_LEASE_LOST'):
        with _connect() as conn, conn.cursor() as cur:
            finish(cur, first, 'completed')
    with _connect() as conn, conn.cursor() as cur:
        finish(cur, second, 'completed')
    with _connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT outcome FROM pipeline_lease_history WHERE scope=%s ORDER BY generation', (scope,))
        assert [r[0] for r in cur.fetchall()] == ['abandoned', 'completed']
