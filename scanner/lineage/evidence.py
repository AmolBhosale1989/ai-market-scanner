"""Capture at the real point_in_time boundary before filtering or OHLCV reduction."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .models import Binding, PublicationBlocked, ReadSeal, digest, json_value, timestamp
from .persistence import Artifact, read_artifact


class ReadCapture:
    def __init__(self,binding: Binding):
        self.binding=binding
        self.records=[]

    def read(self,sql,conn,params,*,req,as_of,visibility,lower,catalogue,catalogue_params,upper):
        if timestamp(as_of)!=self.binding.t0_utc or visibility!=self.binding.pg_snapshot:
            raise PublicationBlocked('LINEAGE_READ_BINDING_MISMATCH')
        wanted=sorted(set(str(s).upper() for s in req.tickers))
        q=dict(consumer=req.consumer,requested_tickers=wanted,as_of_utc=timestamp(as_of).isoformat(),
            event_lower_utc=timestamp(lower).isoformat() if lower is not None else None,
            event_upper_utc=timestamp(upper).isoformat(),timeframe=req.timeframe,data_type=req.data_type,
            sql=sql,parameters=json_value(params),pg_snapshot=visibility,period=req.period)
        try:
            raw=pd.read_sql_query(sql,conn,params=params)
            cat_sql=f'''WITH {catalogue} SELECT revision_id,instrument_id,canonical_symbol,known_at,
                writer_xid::text FROM visible_instrument WHERE canonical_symbol=ANY(%s)
                ORDER BY canonical_symbol,instrument_id'''
            cat=pd.read_sql_query(cat_sql,conn,params=catalogue_params+(wanted,))
            catalogue_rows=cat.to_dict('records'); inventories=[]
            raw_groups={k:v.to_dict('records') for k,v in raw.groupby('ticker')} if not raw.empty else {}
            for symbol in wanted:
                cs=[dict(instrument_id=str(r['instrument_id']),catalogue_revision_id=str(r['revision_id']),
                    writer_xid=str(r['writer_xid']),known_at_utc=timestamp(r['known_at']).isoformat())
                    for r in catalogue_rows if r['canonical_symbol']==symbol]
                rs=[dict(observation_id=str(r['observation_id']),instrument_id=str(r['instrument_id']),
                    writer_xid=str(r['writer_xid']),event_timestamp_utc=timestamp(r['event_timestamp']).isoformat(),
                    ingested_at_utc=timestamp(r['ingested_at']).isoformat())
                    for r in raw_groups.get(symbol,[])]
                state='OBSERVATIONS_RETURNED' if rs else ('NO_OBSERVATIONS_IN_SCOPED_RESULT' if cs else 'NO_VISIBLE_CATALOGUE_MATCH')
                inventories.append(dict(symbol=symbol,state=state,catalogue=cs,revisions=rs,
                    absence_certificate=None if rs else dict(scope_query_hash=digest(q))))
            values=json_value(raw.to_dict('records'));cats=json_value(catalogue_rows)
            doc=dict(binding=self.binding.record(),outcome='COMPLETE_RESULT',query=q,query_hash=digest(q),
                raw_result=values,raw_result_hash=digest(values),catalogue_result=cats,catalogue_result_hash=digest(cats),
                catalogue_sql=cat_sql,catalogue_parameters=json_value(catalogue_params+(wanted,)),symbols=inventories)
            self.records.append(read_artifact(ReadSeal.build(doc),self.binding))
            return raw
        except Exception as exc:
            failure=dict(binding=self.binding.record(),outcome='READ_OR_CAPTURE_FAILED',
                query=q,query_hash=digest(q),error_type=type(exc).__name__)
            self.records.append(read_artifact(ReadSeal.build(failure),self.binding))
            raise


@dataclass(frozen=True)
class WarehouseBatch:
    read: Artifact
    histories: dict
    views: dict
    frame_hashes: dict

    @property
    def binding(self): return self.read.binding

    def verify(self):
        self.read.document()
        if set(self.histories)!=set(self.frame_hashes) or any(frame_hash(v)!=self.frame_hashes[k] for k,v in self.histories.items()):
            raise PublicationBlocked('LINEAGE_CONSUMED_HISTORY_CHANGED')


def frame_hash(frame):
    return digest(dict(index=json_value(list(frame.index)),columns=list(frame.columns),
                       rows=json_value(frame.to_dict('records'))))


def load_inputs(binding,tickers,*,period='5d',connect=None):
    from ..bitemporal_warehouse import PointInTimeRequirement, point_in_time
    from ..warehouse import _assert_quality, _assert_coverage, _freshness_failures
    capture=ReadCapture(binding)
    raw=point_in_time(PointInTimeRequirement('lineage.producer',tuple(tickers),timeframe='5m',
        as_of=binding.t0_utc,period=period,pg_snapshot=binding.pg_snapshot),evidence=capture,connection_factory=connect)
    _assert_quality(raw,'lineage.producer');_assert_coverage(raw,tickers,'lineage.producer')
    stale,_,_=_freshness_failures(raw,'5m',10,'lineage.producer',now_utc=binding.t0_utc)
    if stale: raise PublicationBlocked('LINEAGE_INPUT_STALE_AT_T0:'+','.join(stale))
    read=capture.records[-1];histories={};views={}
    for symbol,group in raw.groupby('ticker'):
        frame=group.set_index(pd.to_datetime(group.event_timestamp,utc=True)).rename(columns={
            'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
        histories[symbol]=frame[['Open','High','Low','Close','Volume']].astype(float).sort_index()
        refs=[dict(observation_id=str(r['observation_id']),event_timestamp_utc=timestamp(r['event_timestamp']).isoformat())
              for r in group.to_dict('records')]
        views[symbol]=Artifact.build(binding,'VIEW',dict(authority_schema='LIVE_VIEW_V1',
            symbol=symbol,timeframe='5m',source_refs=refs,consumed_frame_hash=frame_hash(histories[symbol])),[read.content_hash])
    return WarehouseBatch(read,histories,views,{k:frame_hash(v) for k,v in histories.items()})
