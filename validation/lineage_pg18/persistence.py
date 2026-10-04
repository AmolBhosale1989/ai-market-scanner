"""Local review adapter: immutable evidence and caller-owned publication writes.

NOT a replacement for scanner.control_plane.publish. No connections are opened,
no transaction is committed, no provider request is made. PostgreSQL integration
and production role/privilege review MUST pass before enabling these hooks.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4

from dependency_suppression import Binding, PublicationBlocked, graph_digest
from pit_evidence import ReadSeal, digest as read_digest
from rotation_lineage import digest as rotation_digest

class Cursor(Protocol):
    def execute(self, query: str, params: tuple = ()) -> Any: ...
    def fetchone(self) -> Any: ...


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def timestamp(value: Any) -> datetime:
    try:
        out = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(out, datetime) or out.tzinfo is None or out.utcoffset() is None:
            raise ValueError()
        return out.astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise PublicationBlocked('PERSISTENCE_CLOCK_INVALID') from exc


@dataclass(frozen=True)
class Artifact:
    binding: Binding
    kind: str
    text: str
    content_hash: str
    parent_hashes: tuple[str, ...]

    @classmethod
    def build(cls, binding: Binding, kind: str, body: dict, parents=()):
        if kind not in {'READ','READ_FAILURE','VIEW','PRODUCER','PROJECTION'}:
            raise PublicationBlocked('ARTIFACT_KIND_INVALID')
        parent_hashes=tuple(sorted(parents))
        if len(set(parent_hashes))!=len(parent_hashes) or any(
            not re.fullmatch('[0-9a-f]{64}', x) for x in parent_hashes):
            raise PublicationBlocked('ARTIFACT_PARENTS_INVALID')
        if (kind in {'READ','READ_FAILURE'}) != (not parent_hashes):
            raise PublicationBlocked('ARTIFACT_PARENT_INVENTORY_INVALID')
        text=canonical(dict(schema='LINEAGE_DB_DRAFT_V1',kind=kind,binding=binding.record(),
                            parent_hashes=list(parent_hashes),body=body))
        if len(text.encode())>64*1024*1024:
            raise PublicationBlocked('ARTIFACT_TOO_LARGE')
        return cls(binding,kind,text,hash_text(text),parent_hashes)

    def document(self):
        if hash_text(self.text)!=self.content_hash:
            raise PublicationBlocked('ARTIFACT_HASH_MISMATCH')
        d=json.loads(self.text)
        if (d.get('binding')!=self.binding.record() or d.get('kind')!=self.kind or
            d.get('parent_hashes')!=list(self.parent_hashes)):
            raise PublicationBlocked('ARTIFACT_METADATA_MISMATCH')
        return d


def read_artifact(seal: ReadSeal, binding: Binding) -> Artifact:
    d=seal.document(seal.content_hash)
    if d.get('binding')!=binding.record():
        raise PublicationBlocked('READ_BINDING_MISMATCH')
    outcome=d.get('outcome')
    if outcome=='COMPLETE_RESULT':
        for body, hashed in [('query','query_hash'),('raw_result','raw_result_hash'),
                             ('catalogue_result','catalogue_result_hash')]:
            if body not in d or d.get(hashed)!=read_digest(d[body]):
                raise PublicationBlocked('READ_COMPONENT_HASH_MISMATCH')
        if not isinstance(d.get('symbols'),list):
            raise PublicationBlocked('READ_INVENTORY_MISSING')
        kind='READ'
    elif outcome=='READ_OR_CAPTURE_FAILED':
        if any(key in d for key in ('symbols','raw_result','catalogue_result')):
            raise PublicationBlocked('FAILED_READ_IS_NOT_ABSENCE')
        kind='READ_FAILURE'
    else:
        raise PublicationBlocked('READ_OUTCOME_INVALID')
    return Artifact.build(binding,kind,dict(sealed_text=seal.document_json,sealed_hash=seal.content_hash))


def view_artifact(seal: ReadSeal, binding: Binding, parent: Artifact) -> Artifact:
    p=parent.document(); d=seal.document(seal.content_hash)
    if parent.kind!='READ' or parent.binding!=binding or d.get('binding')!=binding.record():
        raise PublicationBlocked('VIEW_PARENT_INVALID')
    if d.get('parent_read_hash')!=p['body']['sealed_hash']:
        raise PublicationBlocked('VIEW_READ_REFERENCE_MISMATCH')
    return Artifact.build(binding,'VIEW',dict(sealed_text=seal.document_json,sealed_hash=seal.content_hash),
                          (parent.content_hash,))


def producer_artifact(seal, originals: Mapping[str,list[dict]], views: Mapping[str,Artifact]) -> Artifact:
    """Persist graph topology AND original rows, not just an unresolvable graph hash."""
    if graph_digest(seal.nodes,seal.policy)!=seal.graph_hash:
        raise PublicationBlocked('PRODUCER_GRAPH_HASH_MISMATCH')
    if {k:rotation_digest(v) for k,v in originals.items()}!=dict(seal.dataset_hashes):
        raise PublicationBlocked('PRODUCER_ORIGINAL_HASH_MISMATCH')
    if any(v.kind!='VIEW' or v.binding!=seal.binding for v in views.values()):
        raise PublicationBlocked('PRODUCER_VIEW_BINDING_MISMATCH')
    # A source node uses the precise view-seal hash; dataframe/ticker identity alone
    # cannot stand in for its read-manifest parent.
    handles={}
    for v in views.values():
        doc=json.loads(v.document()['body']['sealed_text'])
        key=f"ohlcv:{doc['symbol']}:{doc['timeframe']}:{v.document()['body']['sealed_hash']}"
        handles[key]=v.content_hash
    roots={n.node_id for n in seal.nodes if n.kind=='source'}
    if roots!=set(handles):
        raise PublicationBlocked('PRODUCER_VIEW_INVENTORY_MISMATCH')
    refs={}
    for ref in seal.row_bindings:
        key=(ref.dataset,ref.ordinal)
        if key in refs or ref.dataset not in originals or not 0<=ref.ordinal<len(originals[ref.dataset]):
            raise PublicationBlocked('PRODUCER_ROW_REFERENCE_INVALID')
        if rotation_digest(originals[ref.dataset][ref.ordinal])!=ref.row_hash:
            raise PublicationBlocked('PRODUCER_ROW_HASH_MISMATCH')
        refs[key]=ref
    if len(refs)!=sum(map(len,originals.values())):
        raise PublicationBlocked('PRODUCER_ROW_INVENTORY_INCOMPLETE')
    body=dict(seal=seal.record(),sidecar_hash=seal.sidecar_hash,
              policy=dict(version=seal.policy.version,optional_sources=sorted(seal.policy.optional_sources)),
              nodes=[dict(node_id=n.node_id,kind=n.kind,depends_on=list(n.depends_on)) for n in seal.nodes],
              source_artifacts=handles,original_datasets=dict(originals))
    return Artifact.build(seal.binding,'PRODUCER',body,[v.content_hash for v in views.values()])


def store_artifact(cur: Cursor, artifact: Artifact) -> None:
    artifact.document()
    cur.execute('''INSERT INTO lineage_draft.artifact
      (pipeline_run_id,content_hash,kind,document_text,parent_hashes,depth)
      VALUES(%s,%s,%s,%s,%s,0) ON CONFLICT(pipeline_run_id,content_hash) DO NOTHING''',
      (artifact.binding.run_id,artifact.content_hash,artifact.kind,artifact.text,list(artifact.parent_hashes)))
    cur.execute('''SELECT kind,document_text,parent_hashes FROM lineage_draft.artifact
      WHERE pipeline_run_id=%s AND content_hash=%s''',(artifact.binding.run_id,artifact.content_hash))
    row=cur.fetchone()
    if row is None or tuple(row)!=(artifact.kind,artifact.text,list(artifact.parent_hashes)):
        raise PublicationBlocked('PERSISTED_ARTIFACT_CONFLICT')


def register_context(cur: Cursor, binding: Binding, catalogue_hashes: Mapping[str,str]):
    if set(catalogue_hashes)!={'master_universe','live_universe','tradable_universe'}:
        raise PublicationBlocked('CATALOGUE_INVENTORY_INVALID')
    if any(not re.fullmatch('[0-9a-f]{64}',v) for v in catalogue_hashes.values()):
        raise PublicationBlocked('CATALOGUE_HASH_INVALID')
    cur.execute('''INSERT INTO lineage_draft.run_context
      (pipeline_run_id,t0,snapshot_text,source_commit,policy_version,catalogue_hashes)
      VALUES(%s,%s,%s,%s,%s,%s::jsonb)''',
      (binding.run_id,binding.t0_utc,binding.pg_snapshot,binding.source_commit,binding.policy_version,
       canonical(dict(catalogue_hashes))))


def record_publication_certificate(cur: Cursor, snapshot_id: int, projection: Artifact):
    """Call inside the existing publication transaction, after final projection.

    No freshness verdict is fabricated here. Body is supplied by the still-to-be-
    integrated calendar/graph authority. Persist only a same-attempt projection.
    SQL enforces same-XID projection+certificate+intent; original evidence can be
    retained in earlier forensic transactions.
    """
    d=projection.document()['body']
    if projection.kind!='PROJECTION':
        raise PublicationBlocked('PUBLICATION_PROJECTION_REQUIRED')
    checked,until=timestamp(d['checked_at_utc']),timestamp(d['authorized_until_utc'])
    if checked<projection.binding.t0_utc or until<=checked:
        raise PublicationBlocked('PUBLICATION_WINDOW_INVALID')
    eligible=d.get('eligible_signals')
    if not isinstance(eligible,list):
        raise PublicationBlocked('PROJECTION_SIGNAL_INVENTORY_MISSING')
    keys=set()
    for row in eligible:
        if (not isinstance(row,dict) or not row.get('node_id') or
            not re.fullmatch('[0-9a-f]{64}',row.get('row_hash','')) or row['node_id'] in keys):
            raise PublicationBlocked('PROJECTION_SIGNAL_INVENTORY_INVALID')
        keys.add(row['node_id'])
    store_artifact(cur,projection)
    cur.execute('''INSERT INTO lineage_draft.publication_evidence
      (publication_snapshot_id,pipeline_run_id,projection_hash,checked_at,authorized_until)
      VALUES(%s,%s,%s,%s,%s)''',(snapshot_id,projection.binding.run_id,projection.content_hash,checked,until))


def record_alert_intent(cur: Cursor, snapshot_id: int, projection: Artifact, *,
                        recipient_key: str, semantic_key: str, node_id: str,
                        row_hash: str, expires_at: datetime, payload: dict) -> str:
    if not recipient_key.strip() or not semantic_key.strip() or not isinstance(payload,dict):
        raise PublicationBlocked('ALERT_INTENT_INVALID')
    d=projection.document()['body']; until=timestamp(expires_at)
    if projection.kind!='PROJECTION' or until>timestamp(d['authorized_until_utc']):
        raise PublicationBlocked('ALERT_FRESHNESS_BOUND_INVALID')
    if dict(node_id=node_id,row_hash=row_hash) not in d.get('eligible_signals',[]):
        raise PublicationBlocked('ALERT_SIGNAL_SUPPRESSED_OR_UNKNOWN')
    text=canonical(payload); iid=str(uuid4())
    # Duplicate semantic keys deliberately raise. The policy for retaining an old
    # intent vs linking a repeated event to a new publication is not silently chosen.
    cur.execute('''INSERT INTO lineage_draft.alert_intent
      (intent_id,publication_snapshot_id,pipeline_run_id,projection_hash,channel,
       recipient_key,semantic_key,signal_node_id,row_hash,expires_at,payload_text,payload_hash)
      VALUES(%s,%s,%s,%s,'telegram',%s,%s,%s,%s,%s,%s,%s)''',
      (iid,snapshot_id,projection.binding.run_id,projection.content_hash,recipient_key,semantic_key,
       node_id,row_hash,until,text,hash_text(text)))
    return iid


CLAIM_SQL='''WITH selected AS (
 SELECT d.intent_id FROM lineage_draft.alert_delivery d
 JOIN lineage_draft.alert_intent i USING(intent_id)
 JOIN public.publication_snapshot p USING(publication_snapshot_id)
 JOIN public.publication_head h ON h.mode=p.mode AND h.publication_snapshot_id=p.publication_snapshot_id
 WHERE d.state IN ('PENDING','RETRYABLE') AND d.not_before<=clock_timestamp()
   AND p.status='PUBLISHED' AND i.expires_at>clock_timestamp()
 ORDER BY d.not_before,d.intent_id LIMIT 1 FOR UPDATE OF d SKIP LOCKED
) UPDATE lineage_draft.alert_delivery d SET state='CLAIMED',lease_owner=%s,
 lease_until=clock_timestamp()+make_interval(secs=>%s),fence=fence+1
 FROM selected s WHERE d.intent_id=s.intent_id RETURNING d.intent_id,d.fence'''


def claim_one(cur: Cursor, owner: str, lease_seconds: int=30):
    UUID(owner)
    if isinstance(lease_seconds,bool) or not 1<=lease_seconds<=120:
        raise PublicationBlocked('CLAIM_LEASE_INVALID')
    cur.execute(CLAIM_SQL,(owner,lease_seconds))
    return cur.fetchone()


def classify_telegram_reply(http_status: int | None, body: Any) -> str:
    """Pure policy helper: no send, no retry, no database mutation.

    A timeout or missing response is ambiguous. Conservative quarantine sacrifices
    delivery liveness; this is NOT an at-least-once or exactly-once guarantee.
    """
    if not isinstance(body,dict): return 'DELIVERY_UNKNOWN'
    mid=body.get('result',{}).get('message_id') if isinstance(body.get('result'),dict) else None
    if http_status==200 and body.get('ok') is True and type(mid) is int and mid>0:
        return 'DELIVERED'
    retry=body.get('parameters',{}).get('retry_after') if isinstance(body.get('parameters'),dict) else None
    if (body.get('ok') is False and body.get('error_code')==429 and
        type(retry) is int and 0<retry<=86400): return 'RETRYABLE'
    if body.get('ok') is False and body.get('error_code') in {400,401,403,404}:
        return 'FAILED'
    return 'DELIVERY_UNKNOWN'
