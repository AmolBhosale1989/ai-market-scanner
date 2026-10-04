"""Explicit lineage entrypoint; not activated by legacy Feeder/Engine workflows.

Caller supplies an already registered Stage-70 binding and a scanner-writer
connection factory. Source reads, diagnostics and publication never share one
long transaction. No external alert transport runs here.
"""
from .evidence import load_inputs
from .persistence import Artifact
from .publication import commit_diagnostics,publish_from_committed_producer
from ..producers.rotation_producer import calculate_rotation_eligibility
from ..producers.momentum_producer import calculate_momentum_eligibility


def generate_rotation_publication(connect,binding,universe_tickers,*,momentum_limit=40):
    batch=load_inputs(binding,universe_tickers,connect=connect)
    _,rotation=calculate_rotation_eligibility(batch)
    _,momentum=calculate_momentum_eligibility(batch,rotation,limit=momentum_limit)
    signals=[*rotation.to_dict()['body']['signals'],*momentum.to_dict()['body']['signals']]
    parent_hashes={rotation.hash,momentum.hash,*[v.content_hash for v in batch.views.values()]}
    joint=Artifact.build(binding,'PRODUCER',dict(authority_schema='PRODUCER_AUTHORITY_V1',
        producer_name='rotation_momentum_join',source_commit=binding.source_commit,signals=signals,
        upstream_producer_hashes=[rotation.hash,momentum.hash]),parent_hashes)
    commit_diagnostics(connect,[batch.read,*batch.views.values(),rotation.artifact,momentum.artifact,joint])
    return publish_from_committed_producer(connect,joint)
