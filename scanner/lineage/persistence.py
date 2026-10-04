"""Canonical evidence, explicit transaction cursors, and no hidden commits."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from uuid import UUID,uuid4
from .models import Binding,PublicationBlocked,ReadSeal,digest,timestamp


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def hash_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Artifact:
    binding: Binding
    kind: str
    text: str
    content_hash: str
    parent_hashes: tuple[str,...]

    @classmethod
    def build(cls,binding,kind,body,parents=()):
        if kind not in {'READ','READ_FAILURE','VIEW','PRODUCER','PROJECTION'}:
            raise PublicationBlocked('ARTIFACT_KIND_INVALID')
        parents=tuple(sorted(parents))
        if len(set(parents))!=len(parents) or any(not re.fullmatch('[0-9a-f]{64}',p) for p in parents):
            raise PublicationBlocked('ARTIFACT_PARENTS_INVALID')
        if (kind in {'READ','READ_FAILURE'}) != (not parents):
            raise PublicationBlocked('ARTIFACT_PARENT_INVENTORY_INVALID')
        text=canonical(dict(schema='LINEAGE_DB_V1',kind=kind,binding=binding.record(),parent_hashes=list(parents),body=body))
        if len(text.encode())>64*1024*1024:raise PublicationBlocked('ARTIFACT_TOO_LARGE')
        return cls(binding,kind,text,hash_text(text),parents)

    def document(self):
        if hash_text(self.text)!=self.content_hash:raise PublicationBlocked('ARTIFACT_HASH_MISMATCH')
        d=json.loads(self.text)
        if d.get('binding')!=self.binding.record() or d.get('kind')!=self.kind or d.get('parent_hashes')!=list(self.parent_hashes):
            raise PublicationBlocked('ARTIFACT_METADATA_MISMATCH')
        return d


def read_artifact(seal,binding):
    d=seal.document(seal.content_hash)
    if d.get('binding')!=binding.record():raise PublicationBlocked('READ_BINDING_MISMATCH')
    if d.get('outcome')=='COMPLETE_RESULT':
        for value,key in [('query','query_hash'),('raw_result','raw_result_hash'),('catalogue_result','catalogue_result_hash')]:
            if value not in d or d.get(key)!=digest(d[value]):raise PublicationBlocked('READ_COMPONENT_HASH_MISMATCH')
        if not isinstance(d.get('symbols'),list):raise PublicationBlocked('READ_INVENTORY_MISSING')
        kind='READ'
    elif d.get('outcome')=='READ_OR_CAPTURE_FAILED':
        if any(k in d for k in ('symbols','raw_result','catalogue_result')):raise PublicationBlocked('FAILED_READ_IS_NOT_ABSENCE')
        kind='READ_FAILURE'
    else:raise PublicationBlocked('READ_OUTCOME_INVALID')
    return Artifact.build(binding,kind,dict(sealed_text=seal.document_json,sealed_hash=seal.content_hash))


def store_artifact(cur,artifact):
    artifact.document()
    cur.execute('''INSERT INTO lineage.artifact(pipeline_run_id,content_hash,kind,document_text,parent_hashes,depth)
      VALUES(%s,%s,%s,%s,%s,0) ON CONFLICT(pipeline_run_id,content_hash) DO NOTHING''',
      (artifact.binding.run_id,artifact.content_hash,artifact.kind,artifact.text,list(artifact.parent_hashes)))
    cur.execute('SELECT kind,document_text,parent_hashes FROM lineage.artifact WHERE pipeline_run_id=%s AND content_hash=%s',
        (artifact.binding.run_id,artifact.content_hash))
    row=cur.fetchone()
    if row is None or tuple(row)!=(artifact.kind,artifact.text,list(artifact.parent_hashes)):
        raise PublicationBlocked('PERSISTED_ARTIFACT_CONFLICT')


def register_context(cur,binding,catalogue_hashes):
    if set(catalogue_hashes)!={'master_universe','live_universe','tradable_universe'} or any(not re.fullmatch('[0-9a-f]{64}',v) for v in catalogue_hashes.values()):
        raise PublicationBlocked('CATALOGUE_INVENTORY_INVALID')
    cur.execute('''INSERT INTO lineage.run_context(pipeline_run_id,t0,snapshot_text,source_commit,policy_version,catalogue_hashes)
      VALUES(%s,%s,%s,%s,%s,%s::jsonb)''',(binding.run_id,binding.t0_utc,binding.pg_snapshot,binding.source_commit,binding.policy_version,canonical(dict(catalogue_hashes))))


def record_publication_certificate(cur,snapshot_id,projection):
    d=projection.document()['body']
    if projection.kind!='PROJECTION':raise PublicationBlocked('PUBLICATION_PROJECTION_REQUIRED')
    checked,until=timestamp(d['checked_at_utc']),timestamp(d['authorized_until_utc'])
    if checked<projection.binding.t0_utc or until<=checked:raise PublicationBlocked('PUBLICATION_WINDOW_INVALID')
    eligible=d.get('eligible_signals');keys=set()
    if not isinstance(eligible,list):raise PublicationBlocked('PROJECTION_SIGNAL_INVENTORY_MISSING')
    for s in eligible:
        if not isinstance(s,dict) or not s.get('node_id') or not re.fullmatch('[0-9a-f]{64}',s.get('row_hash','')) or s['node_id'] in keys:
            raise PublicationBlocked('PROJECTION_SIGNAL_INVENTORY_INVALID')
        keys.add(s['node_id'])
    store_artifact(cur,projection)
    cur.execute('''INSERT INTO lineage.publication_evidence(publication_snapshot_id,pipeline_run_id,projection_hash,checked_at,authorized_until)
      VALUES(%s,%s,%s,%s,%s)''',(snapshot_id,projection.binding.run_id,projection.content_hash,checked,until))


def record_alert_intent(cur,snapshot_id,projection,*,recipient_key,semantic_key,node_id,row_hash,expires_at,payload):
    if not recipient_key.strip() or not semantic_key.strip() or not isinstance(payload,dict):raise PublicationBlocked('ALERT_INTENT_INVALID')
    d=projection.document()['body'];until=timestamp(expires_at)
    if projection.kind!='PROJECTION' or until>timestamp(d['authorized_until_utc']):raise PublicationBlocked('ALERT_FRESHNESS_BOUND_INVALID')
    if dict(node_id=node_id,row_hash=row_hash) not in d.get('eligible_signals',[]):raise PublicationBlocked('ALERT_SIGNAL_SUPPRESSED_OR_UNKNOWN')
    text=canonical(payload);iid=str(uuid4())
    cur.execute('''INSERT INTO lineage.alert_intent(intent_id,publication_snapshot_id,pipeline_run_id,projection_hash,channel,
      recipient_key,semantic_key,signal_node_id,row_hash,expires_at,payload_text,payload_hash)
      VALUES(%s,%s,%s,%s,'telegram',%s,%s,%s,%s,%s,%s,%s) RETURNING intent_id''',
      (iid,snapshot_id,projection.binding.run_id,projection.content_hash,recipient_key,semantic_key,node_id,row_hash,until,text,hash_text(text)))
    inserted=cur.fetchone()
    return str(inserted[0]) if inserted else None


CLAIM_SQL='''WITH selected AS (
 SELECT d.intent_id FROM lineage.alert_delivery d JOIN lineage.alert_intent i USING(intent_id)
 JOIN public.publication_snapshot p USING(publication_snapshot_id)
 JOIN public.publication_head h ON h.mode=p.mode AND h.publication_snapshot_id=p.publication_snapshot_id
 WHERE d.state IN ('PENDING','RETRYABLE') AND d.not_before<=clock_timestamp()
 AND p.status='PUBLISHED' AND i.expires_at>clock_timestamp()
 ORDER BY d.not_before,d.intent_id LIMIT 1 FOR UPDATE OF d SKIP LOCKED
) UPDATE lineage.alert_delivery d SET state='CLAIMED',lease_owner=%s,
 lease_until=clock_timestamp()+make_interval(secs=>%s),fence=fence+1
 FROM selected s WHERE d.intent_id=s.intent_id RETURNING d.intent_id,d.fence'''


def claim_one(cur,owner,lease_seconds=30):
    UUID(owner)
    if isinstance(lease_seconds,bool) or not 1<=lease_seconds<=120:raise PublicationBlocked('CLAIM_LEASE_INVALID')
    cur.execute(CLAIM_SQL,(owner,lease_seconds));return cur.fetchone()
