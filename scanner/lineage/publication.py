"""Two separately owned transactions: durable diagnostics, then atomic publication."""
from __future__ import annotations
from datetime import timedelta
from uuid import uuid4
import json

from .persistence import Artifact, canonical, hash_text, store_artifact
from .persistence import record_publication_certificate, record_alert_intent


def view_for_read(read, refs):
    return Artifact.build(read.binding, 'VIEW', {
        'authority_schema': 'LIVE_VIEW_V1', 'source_refs': refs,
    }, [read.content_hash])


def declared_producer(binding, views, rows, dataset='rotation_eligible'):
    """Producer calls with its ORIGINAL rows and declared consumed views.

    Hashes establish content identity, not a proof of computation against a
    malicious database owner. Producer/runtime roles remain an integration gate.
    """
    declarations = []
    for row in rows:
        node = row['dependency_node_id']
        text = canonical(row)
        declarations.append(dict(node_id=node, dataset_name=dataset, row_text=text,
                                 row_hash=hash_text(text), view_hashes=[v.content_hash for v in views]))
    return Artifact.build(binding, 'PRODUCER', {
        'authority_schema': 'PRODUCER_AUTHORITY_V1', 'signals': declarations,
    }, [v.content_hash for v in views])


def commit_diagnostics(connect, artifacts):
    """Caller-owned separate transaction, before any publication attempt exists."""
    with connect() as db:
        with db.cursor() as cur:
            for a in artifacts:
                store_artifact(cur, a)
        # psycopg connection context commits; no provider/network side effect.
    return artifacts[-1]


def build_projection(cur, producer, *, budget_seconds=None):
    cur.execute('SELECT clock_timestamp()')
    at = cur.fetchone()[0]
    cur.execute('''SELECT signal_node_id,row_hash,source_expires_at FROM lineage.signal_authority
      WHERE pipeline_run_id=%s AND producer_hash=%s ORDER BY signal_node_id''',
      (producer.binding.run_id, producer.content_hash))
    survivors = [r for r in cur.fetchall() if r[2] > at]
    if not survivors:
        raise RuntimeError('NO_AUTHORIZED_LIVE_SIGNAL')
    until = min(r[2] for r in survivors)
    if budget_seconds is not None:
        # A shorter publication authorization is legal; extending source life is not.
        until = min(until, at + timedelta(seconds=budget_seconds))
    return Artifact.build(producer.binding, 'PROJECTION', dict(
        checked_at_utc=at.isoformat(), authorized_until_utc=until.isoformat(),
        eligible_signals=[dict(node_id=n, row_hash=h) for n,h,_ in survivors]), [producer.content_hash])


def stage_publication(cur, producer, *, projection=None, physical_rows=True, mode='production', recipient_key='local-recipient'):
    """No commit. Uses real public publication/dataset schema in the CI database.

    Physical rows precede the certificate/intents. They remain uncommitted until
    the caller's entire publication transaction succeeds.
    """
    projection = projection or build_projection(cur, producer)
    binding = producer.binding
    cur.execute('''INSERT INTO public.publication_snapshot(pipeline_run_id,mode,status)
      VALUES(%s,%s,'VALIDATING') RETURNING publication_snapshot_id''', (binding.run_id, mode))
    sid = cur.fetchone()[0]
    if physical_rows:
        cur.execute('''SELECT signal_node_id,row_hash,dataset_name,row_text FROM lineage.signal_authority
          WHERE pipeline_run_id=%s AND producer_hash=%s ORDER BY signal_node_id''',
          (binding.run_id,producer.content_hash))
        allowed = {(s['node_id'],s['row_hash']) for s in projection.document()['body']['eligible_signals']}
        groups = {}
        for node, digest, dataset, text in cur.fetchall():
            if (node,digest) in allowed:
                groups.setdefault(dataset,[]).append(json.loads(text))
        for dataset, rows in groups.items():
            cur.execute('''INSERT INTO public.dataset_version
              (pipeline_run_id,dataset_name,status,row_count,content_hash)
              VALUES(%s,%s,'AVAILABLE',%s,%s) RETURNING dataset_version_id''',
              (binding.run_id,dataset,len(rows),hash_text(canonical(rows))))
            version = cur.fetchone()[0]
            for ordinal,row in enumerate(rows):
                cur.execute('INSERT INTO public.dataset_row VALUES(%s,%s,%s,%s::jsonb)',
                            (version,ordinal,row.get('ticker'),canonical(row)))
            cur.execute('INSERT INTO public.publication_dataset VALUES(%s,%s,%s)', (sid,dataset,version))
    record_publication_certificate(cur,sid,projection)
    intents=[]
    for signal in sorted(projection.document()['body']['eligible_signals'],key=lambda r:r['node_id']):
        original=cur.execute('SELECT row_text FROM lineage.signal_authority WHERE pipeline_run_id=%s AND producer_hash=%s AND signal_node_id=%s',(binding.run_id,producer.content_hash,signal['node_id'])).fetchone()
        if original and not json.loads(original[0]).get('meets_alert_threshold',False):
            continue
        intents.append(record_alert_intent(cur,sid,projection,recipient_key=recipient_key,
            semantic_key=signal['node_id'],node_id=signal['node_id'],row_hash=signal['row_hash'],
            expires_at=__import__('datetime').datetime.fromisoformat(projection.document()['body']['authorized_until_utc']),
            payload={'text':'CI synthetic signal, never send externally', 'node_id':signal['node_id']}))
    cur.execute("UPDATE public.publication_snapshot SET status='PUBLISHED',published_at=clock_timestamp() WHERE publication_snapshot_id=%s",(sid,))
    cur.execute('''INSERT INTO public.publication_head(mode,publication_snapshot_id) VALUES(%s,%s)
      ON CONFLICT(mode) DO UPDATE SET publication_snapshot_id=EXCLUDED.publication_snapshot_id,
      updated_at=clock_timestamp()''',(mode,sid))
    return sid,intents[0] if intents else None,projection


def publish_from_committed_producer(connect, producer):
    with connect() as db:
        with db.cursor() as cur:
            result=stage_publication(cur,producer)
    return result
