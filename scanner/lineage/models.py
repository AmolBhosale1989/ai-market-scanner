"""Immutable, canonical lineage values. Hashes bind content, not proof of execution."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import re
from uuid import UUID
import pandas as pd

class PublicationBlocked(RuntimeError):
    pass


def timestamp(value):
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:
        raise PublicationBlocked('LINEAGE_CLOCK_INVALID')
    return value.tz_convert('UTC').to_pydatetime()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def json_value(value):
    """Typed raw-read encoding; unlike display JSON, nonfinite values stay explicit."""
    if isinstance(value,dict): return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_value(v) for v in value]
    if value is pd.NA or value is pd.NaT: return {'missing':str(value)}
    if isinstance(value,datetime): return {'datetime':value.isoformat()}
    if isinstance(value,Decimal): return {'decimal':str(value)}
    if isinstance(value,UUID): return {'uuid':str(value)}
    if hasattr(value,'item'): return json_value(value.item())
    if isinstance(value,float) and not math.isfinite(value): return {'float':str(value)}
    return value


@dataclass(frozen=True)
class Binding:
    run_id: str
    t0_utc: datetime
    pg_snapshot: str
    source_commit: str
    policy_version: str

    def __post_init__(self):
        UUID(str(self.run_id))
        object.__setattr__(self,'t0_utc',timestamp(self.t0_utc))
        if not self.pg_snapshot or not self.policy_version or not re.fullmatch('[0-9a-f]{40}',self.source_commit):
            raise PublicationBlocked('LINEAGE_BINDING_INVALID')

    def record(self):
        return dict(run_id=self.run_id,t0_utc=self.t0_utc.isoformat(),pg_snapshot=self.pg_snapshot,
                    source_commit=self.source_commit,policy_version=self.policy_version)


@dataclass(frozen=True)
class ReadSeal:
    document_json: str
    content_hash: str

    @classmethod
    def build(cls,document,max_bytes=64*1024*1024):
        text=canonical(document)
        if len(text.encode())>max_bytes: raise PublicationBlocked('LINEAGE_EVIDENCE_TOO_LARGE')
        return cls(text,hashlib.sha256(text.encode()).hexdigest())

    def document(self,expected_hash):
        if self.content_hash!=expected_hash or hashlib.sha256(self.document_json.encode()).hexdigest()!=expected_hash:
            raise PublicationBlocked('LINEAGE_READ_HASH_MISMATCH')
        return json.loads(self.document_json)
