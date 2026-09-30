"""Bounded Alpha batch writer: pipelined SQL, legacy-compatible locks/revisions."""
import json
import uuid

import pandas as pd

from .catalyst_warehouse import _canonical_payload
from .execution_timing import phase


def persist_alpha(cur, provider, wanted, indexed, anchor):
    provider = str(provider).strip()
    if not provider or not wanted or pd.Timestamp(anchor).tzinfo is None:
        raise ValueError('CATALYST_BATCH_IDENTITY_OR_ANCHOR_INVALID')
    plans, events = {}, []
    with phase('normalization'):
        for ticker in wanted:
            fetched = indexed.get(ticker)
            raw = () if fetched is None else fetched.events
            rejected = 0 if fetched is None else fetched.rejected_count
            if rejected < 0:
                raise ValueError('rejected_count must be nonnegative')
            ids = set()
            rows = []
            for event in raw:
                event_id = str(event.provider_event_id).strip()
                if not event_id or event_id in ids:
                    raise RuntimeError('CATALYST_DUPLICATE_OR_INVALID_EVENT_ID_IN_FETCH')
                ids.add(event_id)
                at = pd.Timestamp(event.event_timestamp)
                if at.tzinfo is None:
                    raise RuntimeError('CATALYST_EVENT_TIME_NAIVE')
                encoded, digest = _canonical_payload(dict(event.payload))
                rows.append((ticker, event_id, str(event.catalyst_type),
                             at.tz_convert('UTC').to_pydatetime(), encoded, digest))
            plans[ticker] = dict(run_id=str(uuid.uuid4()), rejected=rejected, rows=rows)
            events.extend(rows)

    # Same batch and event advisory keys as single-symbol ingestion; acquire
    # deterministically, then inspect active revisions under READ COMMITTED.
    sep = '\\x1f'
    with phase('advisory_locks'):
        cur.executemany('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                        [(f'catalyst-batch{sep}{provider}{sep}{t}',) for t in sorted(wanted)])
        if events:
            keys = sorted(f'{provider}{sep}{e[1]}{sep}{e[0]}' for e in events)
            cur.executemany('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                            [(key,) for key in keys])
    with phase('instrument_lookup'):
        cur.execute('''SELECT DISTINCT ON (canonical_symbol) canonical_symbol,instrument_id
                       FROM instrument WHERE canonical_symbol=ANY(%s)
                       ORDER BY canonical_symbol,instrument_id''', (wanted,))
        instruments = dict(cur.fetchall())
        missing = sorted(set(wanted)-instruments.keys())
        if missing:
            raise RuntimeError(f'CATALYST_INSTRUMENT_UNKNOWN: {missing[0]}')
    with phase('run_insert'):
        cur.executemany('''INSERT INTO warehouse_run_log
            (warehouse_run_id,provider,request_type,requested_at,status,request_payload)
            VALUES (%s,%s,'CATALYST_CONTEXT',now(),'STARTED',%s::jsonb)''',
            [(p['run_id'], provider, json.dumps({'ticker': t, 'global_batch': True}))
             for t,p in plans.items()])
    active = {}
    with phase('revision_read'):
        if events:
            cur.execute('''SELECT ticker,provider_event_id,catalyst_revision_id,payload_hash
                FROM warehouse_catalyst WHERE provider=%s AND ticker=ANY(%s)
                AND provider_event_id=ANY(%s) AND known_to IS NULL FOR UPDATE''',
                (provider,wanted,list({e[1] for e in events})))
            active = {(t,e):(rid,h) for t,e,rid,h in cur.fetchall()}
        cur.execute('SELECT clock_timestamp()')
        known_at = cur.fetchone()[0]
    changed = [e for e in events if active.get((e[0],e[1]),(None,None))[1] != e[5]]
    with phase('revision_write'):
        closing = [(known_at,active[e[0],e[1]][0]) for e in changed if (e[0],e[1]) in active]
        if closing:
            cur.executemany('''UPDATE warehouse_catalyst SET known_to=%s
                WHERE catalyst_revision_id=%s AND known_to IS NULL''',closing)
            if cur.rowcount != len(closing):
                raise RuntimeError('CATALYST_SUPERSESSION_RACE')
        if changed:
            cur.executemany('''INSERT INTO warehouse_catalyst
                (provider,provider_event_id,instrument_id,ticker,catalyst_type,event_timestamp,
                 known_from,warehouse_run_id,payload_hash,payload)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                [(provider,eid,instruments[t],t,kind,at,known_at,plans[t]['run_id'],digest,payload)
                 for t,eid,kind,at,payload,digest in changed])
    current = {}
    if events:
        with phase('revision_read'):
            cur.execute('''SELECT ticker,provider_event_id,catalyst_revision_id
                FROM warehouse_catalyst WHERE provider=%s AND ticker=ANY(%s)
                AND provider_event_id=ANY(%s) AND known_to IS NULL''',
                (provider,wanted,list({e[1] for e in events})))
            current = {(t,e):rid for t,e,rid in cur.fetchall()}
    with phase('check_insert'):
        cur.executemany('''INSERT INTO catalyst_check
            (provider,instrument_id,ticker,checked_at,warehouse_run_id,result_status,
             event_count,rejected_count,verified_event_ids)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            [(provider,instruments[t],t,anchor,p['run_id'],
              'PROVIDER_PAYLOAD_REJECTED' if p['rejected'] else ('EVENTS' if p['rows'] else 'NO_EVENT'),
              len(p['rows']),p['rejected'],[e[1] for e in p['rows']]) for t,p in plans.items()])
    with phase('run_complete'):
        cur.executemany('''UPDATE warehouse_run_log SET completed_at=now(),status='AVAILABLE',
            response_metadata=%s::jsonb WHERE warehouse_run_id=%s''',
            [(json.dumps({'events':len(p['rows']),'rejected':p['rejected'],'global_batch':True}),p['run_id'])
             for p in plans.values()])
    return {t:dict(events=len(p['rows']),rejected=p['rejected'],revisions=[
        (int(current[t,e[1]]), 'UNCHANGED' if active.get((t,e[1]),(None,None))[1] == e[5]
         else 'SUPERSEDED' if (t,e[1]) in active else 'INSERTED') for e in p['rows']])
        for t,p in plans.items()}
