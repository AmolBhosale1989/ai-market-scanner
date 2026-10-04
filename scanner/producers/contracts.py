"""Producer-owned dependency declarations, separate from display ranks."""
from dataclasses import dataclass
import json
from ..lineage.persistence import Artifact,canonical,hash_text
from ..lineage.models import PublicationBlocked
from ..control_plane import _clean

@dataclass(frozen=True)
class ProducerFragment:
    artifact: Artifact

    @property
    def hash(self): return self.artifact.content_hash
    @property
    def producer_name(self): return self.artifact.document()['body']['producer_name']
    @property
    def nodes(self):
        return {s['node_id']:s for s in self.artifact.document()['body']['signals']}
    def to_dict(self): return self.artifact.document()
    def rows(self,dataset=None):
        return [json.loads(s['row_text']) for s in self.nodes.values() if dataset is None or s['dataset_name']==dataset]


def declare(batch,producer_name,outputs,dependencies,*,decision,upstream=()):
    batch.verify()
    declarations=[];parents={f.hash for f in upstream}
    for dataset,rows in outputs.items():
        for row in rows:
            row=_clean(row);node=row['dependency_node_id']
            symbols=set(dependencies[node])
            if not symbols or not symbols.issubset(batch.views):
                raise PublicationBlocked('PRODUCER_INPUT_EVIDENCE_MISSING:'+node)
            views=[batch.views[s].content_hash for s in sorted(symbols)]
            parents.update(views);text=canonical(row)
            declarations.append(dict(node_id=node,dataset_name=dataset,row_text=text,row_hash=hash_text(text),
                view_hashes=views,source_symbols=sorted(symbols)))
    # A valid empty-result producer still declares its evaluated input inventory.
    if not declarations: parents.update(v.content_hash for v in batch.views.values())
    body=dict(authority_schema='PRODUCER_AUTHORITY_V1',producer_name=producer_name,
        producer_version='shared-core-v1',source_commit=batch.binding.source_commit,
        input_manifest_hash=batch.read.content_hash,signals=declarations,
        upstream_producer_hashes=[f.hash for f in upstream],decision=_clean(decision))
    return ProducerFragment(Artifact.build(batch.binding,'PRODUCER',body,sorted(parents)))
