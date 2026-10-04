"""Actual momentum thresholds; freeze top-N selection before local evaluation."""
import pandas as pd
from .. import momentum_signals as core
from ..lineage.models import PublicationBlocked
from .contracts import declare


def calculate_momentum_eligibility(batch,rotation_fragment,*,limit=40,broad_fragment=None):
    batch.verify()
    upstream=[rotation_fragment]+([broad_fragment] if broad_fragment is not None else [])
    if any(f.artifact.binding!=batch.binding for f in upstream):
        raise PublicationBlocked('MOMENTUM_UPSTREAM_BINDING_MISMATCH')
    themed=pd.DataFrame(rotation_fragment.rows('rotation_eligible'))
    broad=pd.DataFrame(broad_fragment.rows('broad_breakout_discovery')) if broad_fragment else pd.DataFrame()
    selected=core.select_candidates(themed,broad,limit)
    if selected.empty:
        output=[]
    else:
        frame,_=core.evaluate_momentum(selected,batch.histories,presentation=False)
        output=frame.to_dict('records')
    lookup={s['node_id']:s for f in upstream for s in f.nodes.values()}
    meta={str(r['ticker']):r for r in selected.to_dict('records')}
    dependencies={}
    for row in output:
        source=meta[row['ticker']];parent=lookup.get(source.get('dependency_node_id'))
        if parent is None: raise PublicationBlocked('MOMENTUM_SELECTED_NODE_UNDECLARED')
        node=f"MOMENTUM_{source.get('theme_etf','BROAD')}_{row['ticker']}"
        row.update(dependency_node_id=node,theme_etf=source.get('theme_etf',''),
            meets_alert_threshold=row['signal']=='MOMENTUM BUY')
        dependencies[node]=set(parent['source_symbols'])|{row['ticker']}
    fragment=declare(batch,'momentum_v3',{'momentum_eligible':output},dependencies,
        decision=dict(strategy_limit=limit,selected=selected.to_dict('records'),
            selection='ORIGINAL_PRIORITY_DEDUP_TOP_N_AT_T0',no_expiry_backfill=True,
            presentation_rank_applied=False),upstream=upstream)
    return output,fragment
