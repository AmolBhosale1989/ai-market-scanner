"""Use the actual rotation calculator; emit per-theme dependencies without ranks."""
from .. import sector_rotation as core
from .contracts import declare


def calculate_rotation_eligibility(batch):
    batch.verify()
    themes,leaders=core.evaluate_rotation(sorted(batch.histories),batch.histories,presentation=False,persist=False)
    session=core.latest_frame_session(core._extract(batch.histories['SPY'],'SPY'))
    available={s for s,h in batch.histories.items() if core._stats(core._extract(h,s),s,session) is not None}
    dependencies={};stocks=[];theme_rows=[]
    for row in themes.to_dict('records'):
        etf=row['etf'];theme=row['theme'];node='THEME_'+etf
        row.update(dependency_node_id=node,ticker=etf,meets_alert_threshold=False)
        dependencies[node]={'SPY',etf}|(set(core.THEME_CONSTITUENTS[theme])&available)
        theme_rows.append(row)
    for row in leaders.to_dict('records'):
        etf=core.THEME_ETFS[row['theme']];node=f"ROTATION_{etf}_{row['ticker']}"
        row.update(dependency_node_id=node,theme_etf=etf,meets_alert_threshold=bool(row['rotation_leader']))
        dependencies[node]=set(dependencies['THEME_'+etf])|{row['ticker']}
        stocks.append(row)
    fragment=declare(batch,'rotation_v4',{'rotation_eligible':stocks,'rotation_theme_eligible':theme_rows},
        dependencies,decision=dict(universe=sorted(batch.histories),themes=core.THEME_ETFS,
            constituent_sets=core.THEME_CONSTITUENTS,selection='ALL_EVALUATED',presentation_rank_applied=False))
    return stocks,fragment
