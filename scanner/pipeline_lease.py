"""Atomic recovery ownership primitives; deployment wiring is intentionally separate.

Use the same transaction's cursor for assert_owned and publication. A heartbeat
cannot revive an expired owner. Database time is authoritative. Heartbeats must
come from a supervisor checking worker liveness, not only stage boundaries.
"""
from dataclasses import dataclass
from .bitemporal_warehouse import _connect


@dataclass(frozen=True)
class Lease:
    scope: str
    owner_id: str
    generation: int


def _ttl(seconds):
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 30 <= seconds <= 300:
        raise ValueError("Lease TTL must be 30..300 seconds")
    return seconds


def acquire(scope, owner_id, market_session, ttl_seconds=300):
    ttl = _ttl(ttl_seconds)
    with _connect() as conn, conn.cursor() as cur:
        # Serialize the absent-row case as well as replacement of expired rows.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("lease:" + scope,))
        cur.execute("SELECT owner_id,generation,status,expires_at>clock_timestamp() FROM pipeline_lease WHERE scope=%s FOR UPDATE", (scope,))
        old = cur.fetchone()
        if old and old[2] == "running" and old[3]:
            return None
        generation = old[1] + 1 if old else 1
        if old and old[2] == "running":
            cur.execute("""INSERT INTO pipeline_lease_history
                (scope,generation,owner_id,market_session,acquired_at,outcome)
                SELECT scope,generation,owner_id,market_session,acquired_at,'abandoned'
                FROM pipeline_lease WHERE scope=%s""", (scope,))
        cur.execute("""INSERT INTO pipeline_lease
            (scope,owner_id,generation,market_session,status,acquired_at,heartbeat_at,expires_at)
            VALUES (%s,%s,%s,%s,'running',clock_timestamp(),clock_timestamp(),clock_timestamp()+%s*interval '1 second')
            ON CONFLICT (scope) DO UPDATE SET owner_id=EXCLUDED.owner_id,
            generation=EXCLUDED.generation,market_session=EXCLUDED.market_session,
            status=EXCLUDED.status,acquired_at=EXCLUDED.acquired_at,
            heartbeat_at=EXCLUDED.heartbeat_at,expires_at=EXCLUDED.expires_at""",
            (scope,owner_id,generation,market_session,ttl))
    return Lease(scope,str(owner_id),generation)


def heartbeat(lease, ttl_seconds=300):
    ttl = _ttl(ttl_seconds)
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE pipeline_lease SET heartbeat_at=clock_timestamp(),
            expires_at=clock_timestamp()+%s*interval '1 second'
            WHERE scope=%s AND owner_id=%s AND generation=%s AND status='running'
            AND expires_at>clock_timestamp() RETURNING generation""",
            (ttl,lease.scope,lease.owner_id,lease.generation))
        if cur.fetchone() is None:
            raise RuntimeError("PIPELINE_LEASE_LOST")


def assert_owned(cur, lease):
    cur.execute("""SELECT generation FROM pipeline_lease WHERE scope=%s
        AND owner_id=%s AND generation=%s AND status='running'
        AND expires_at>clock_timestamp() FOR UPDATE""",
        (lease.scope,lease.owner_id,lease.generation))
    if cur.fetchone() is None:
        raise RuntimeError("PIPELINE_LEASE_LOST")


def finish(cur, lease, outcome):
    if outcome not in ('completed','failed'):
        raise ValueError("Invalid lease outcome")
    assert_owned(cur, lease)
    cur.execute("""INSERT INTO pipeline_lease_history
        (scope,generation,owner_id,market_session,acquired_at,outcome)
        SELECT scope,generation,owner_id,market_session,acquired_at,%s
        FROM pipeline_lease WHERE scope=%s""", (outcome,lease.scope))
    cur.execute("UPDATE pipeline_lease SET status=%s WHERE scope=%s", (outcome,lease.scope))
