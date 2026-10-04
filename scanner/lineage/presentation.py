"""Rank already validated rows without changing them or creating new candidates."""
from .persistence import canonical,hash_text


def rank_survivors(rows,*,score='rotation_leader_score',limit=None):
    if limit is not None and (type(limit) is not int or limit<0):
        raise ValueError('Display limit must be a nonnegative integer or None')
    ordered=sorted(rows,key=lambda r:(-float(r.get(score) or 0),str(r.get('ticker','')),r['dependency_node_id']))
    if limit is not None: ordered=ordered[:limit]
    return [dict(presentation_rank=i+1,signal_node_id=row['dependency_node_id'],
        row_hash=hash_text(canonical(row)),ticker=row.get('ticker')) for i,row in enumerate(ordered)]
