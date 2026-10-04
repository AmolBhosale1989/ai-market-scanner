"""Fenced dispatcher candidate. No built-in transport and no external sends in CI.

Durable CLAIMED -> SENDING precedes the HTTP boundary. Only expired CLAIMED work
can be requeued. Expired SENDING is terminal UNKNOWN. Every mutation is fenced by
intent+owner+generation+state; a stale resolver cannot overwrite recovery.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import json
import math
import time
from uuid import uuid4
from persistence import claim_one, canonical

@dataclass(frozen=True)
class Claim:
    intent_id: str
    owner: str
    fence: int


def claim(connect, lease_seconds=30):
    owner=str(uuid4())
    with connect() as db:
        with db.cursor() as cur:
            row=claim_one(cur,owner,lease_seconds)
    return Claim(str(row[0]),owner,int(row[1])) if row else None


def begin_sending(connect, item):
    """Commit durable SENDING BEFORE obtaining authorization for the external call."""
    with connect() as db:
        row=db.execute('''UPDATE lineage_draft.alert_delivery SET state='SENDING',attempts=attempts+1
          WHERE intent_id=%s AND state='CLAIMED' AND lease_owner=%s AND fence=%s
            AND lease_until>clock_timestamp() RETURNING intent_id''',
          (item.intent_id,item.owner,item.fence)).fetchone()
    return row is not None


def pre_send(connect,item, margin_seconds=0.1):
    """Re-read DB clock/current publication and exact physical backing after commit."""
    started=time.monotonic()
    with connect() as db:
        row=db.execute('''SELECT i.payload_text,d.lease_until,i.expires_at,e.authorized_until,
            clock_timestamp(),i.publication_snapshot_id,i.pipeline_run_id,i.projection_hash,i.signal_node_id,i.row_hash
          FROM lineage_draft.alert_delivery d JOIN lineage_draft.alert_intent i USING(intent_id)
          JOIN lineage_draft.publication_evidence e USING(publication_snapshot_id)
          JOIN public.publication_snapshot p USING(publication_snapshot_id)
          JOIN public.publication_head h ON h.mode=p.mode AND h.publication_snapshot_id=p.publication_snapshot_id
          WHERE d.intent_id=%s AND d.state='SENDING' AND d.lease_owner=%s AND d.fence=%s
            AND p.status='PUBLISHED' AND d.lease_until>clock_timestamp()
            AND i.expires_at>clock_timestamp() AND e.authorized_until>clock_timestamp()''',
          (item.intent_id,item.owner,item.fence)).fetchone()
        if row is None:
            return None
        db.execute('SELECT lineage_draft.check_published_signal(%s,%s,%s,%s,%s)',row[5:])
    budget=(min(row[1:4])-row[4]).total_seconds()-(time.monotonic()-started)-margin_seconds
    return (json.loads(row[0]),min(5.0,budget)) if budget>0 else None


def classify(status,body):
    if not isinstance(body,dict): return 'DELIVERY_UNKNOWN'
    result=body.get('result')
    if (status==200 and body.get('ok') is True and isinstance(result,dict)
        and type(result.get('message_id')) is int and result['message_id']>0): return 'DELIVERED'
    params=body.get('parameters')
    if (status==429 and body.get('ok') is False and body.get('error_code')==429
        and isinstance(params,dict) and type(params.get('retry_after')) is int
        and 0<params['retry_after']<=86400): return 'RETRYABLE'
    if status in (400,401,403,404) and body.get('ok') is False and body.get('error_code')==status:
        return 'FAILED'
    return 'DELIVERY_UNKNOWN'


def resolve(connect,item,status=None,body=None,max_attempts=3):
    state=classify(status,body)
    receipt=canonical(dict(http_status=status,body=body))
    with connect() as db:
        row=db.execute('''SELECT d.attempts,i.expires_at,clock_timestamp() FROM lineage_draft.alert_delivery d
          JOIN lineage_draft.alert_intent i USING(intent_id)
          WHERE d.intent_id=%s AND d.state='SENDING' AND d.lease_owner=%s AND d.fence=%s
            AND d.lease_until>clock_timestamp() FOR UPDATE OF d''',
          (item.intent_id,item.owner,item.fence)).fetchone()
        if row is None: return 'STALE_RESOLUTION'
        reason=None
        if state=='RETRYABLE' and (row[0]>=max_attempts or
            (row[1]-row[2]).total_seconds()<=body['parameters']['retry_after']):
            state='FAILED';reason='RETRY_BUDGET_OR_FRESHNESS_EXHAUSTED'
        changed=db.execute('''UPDATE lineage_draft.alert_delivery SET state=%s,provider_receipt=%s::jsonb,reason=%s
          WHERE intent_id=%s AND state='SENDING' AND lease_owner=%s AND fence=%s
          RETURNING state''',(state,receipt,reason,item.intent_id,item.owner,item.fence)).fetchone()
    return changed[0] if changed else 'STALE_RESOLUTION'


def recover(connect,limit=100):
    if type(limit) is not int or not 1<=limit<=1000: raise ValueError('Recovery limit invalid')
    with connect() as db:
        rows=db.execute('''WITH picked AS (
          SELECT intent_id,state,fence,lease_owner FROM lineage_draft.alert_delivery
          WHERE state IN ('CLAIMED','SENDING') AND lease_until<=clock_timestamp()
          ORDER BY lease_until,intent_id LIMIT %s FOR UPDATE SKIP LOCKED
        ) UPDATE lineage_draft.alert_delivery d
          SET state=CASE WHEN p.state='SENDING' THEN 'DELIVERY_UNKNOWN' ELSE 'PENDING' END,
              reason=CASE WHEN p.state='SENDING' THEN 'EXPIRED_SENDING_LEASE' ELSE 'EXPIRED_UNSENT_CLAIM' END
          FROM picked p WHERE d.intent_id=p.intent_id AND d.state=p.state AND d.fence=p.fence
            AND d.lease_owner=p.lease_owner RETURNING d.intent_id,d.state,d.fence''',(limit,)).fetchall()
    return rows


def dispatch_once(connect, transport, lease_seconds=30):
    """Transport is an injected no-retry callable(payload, timeout_seconds).

    A transport must bound its total request duration and disable implicit retries.
    This candidate tests dispatch-initiation safety, not exactly-once receipt.
    """
    item=claim(connect,lease_seconds)
    if item is None: return None
    if not begin_sending(connect,item): return 'CLAIM_LOST'
    try:
        permit=pre_send(connect,item)
        if permit is None:
            return resolve(connect,item)
        payload,budget=permit
        status,body=transport(payload,timeout_seconds=budget)
    except Exception:
        status,body=None,None
    return resolve(connect,item,status,body)
