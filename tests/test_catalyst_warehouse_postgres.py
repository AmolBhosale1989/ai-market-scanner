from datetime import timedelta
import json
import uuid
import pandas as pd
import pytest

from scanner import control_plane as cp
from scanner.bitemporal_warehouse import start_run
from scanner.catalyst_warehouse import ingest_catalyst_revision,catalyst_context


@pytest.mark.postgres
def test_catalyst_revisions_are_point_in_time_visible():
    cp.migrate()
    run=start_run("TEST","CATALYST",{})
    from scanner.database import connection
    with connection() as conn,conn.cursor() as cur:
        cur.execute("""INSERT INTO instrument(canonical_symbol)
          VALUES ('ZZCAT') ON CONFLICT DO NOTHING""")
    event=pd.Timestamp("2026-09-25T14:00:00Z").to_pydatetime()
    r1,a1=ingest_catalyst_revision(provider="TEST",provider_event_id="evt-1",ticker="ZZCAT",
        catalyst_type="NEWS",event_timestamp=event,warehouse_run_id=run,payload={"headline":"first"})
    with connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT known_from FROM warehouse_catalyst WHERE catalyst_revision_id=%s",(r1,))
        first_known=cur.fetchone()[0]
    r2,a2=ingest_catalyst_revision(provider="TEST",provider_event_id="evt-1",ticker="ZZCAT",
        catalyst_type="NEWS",event_timestamp=event,warehouse_run_id=run,payload={"headline":"revised"})
    with connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT known_from FROM warehouse_catalyst WHERE catalyst_revision_id=%s",(r2,))
        second_known=cur.fetchone()[0]
    before=catalyst_context(tickers=("ZZCAT",),as_of=first_known,
        start_time=event-timedelta(hours=1),end_time=event)
    after=catalyst_context(tickers=("ZZCAT",),as_of=second_known,
        start_time=event-timedelta(hours=1),end_time=event)
    assert before.iloc[0]["payload"]["headline"]=="first"
    assert after.iloc[0]["payload"]["headline"]=="revised"
    assert (a1,a2)==("INSERTED","SUPERSEDED")


@pytest.mark.postgres
def test_identical_catalyst_is_idempotent():
    cp.migrate()
    run=start_run("TEST","CATALYST",{})
    from scanner.database import connection
    with connection() as conn,conn.cursor() as cur:
        cur.execute("""INSERT INTO instrument(canonical_symbol)
          VALUES ('ZZIDEM') ON CONFLICT DO NOTHING""")
    event=pd.Timestamp("2026-09-25T14:00:00Z").to_pydatetime()
    args=dict(provider="TEST",provider_event_id="same",ticker="ZZIDEM",catalyst_type="NEWS",
              event_timestamp=event,warehouse_run_id=run,payload={"x":1})
    first=ingest_catalyst_revision(**args)
    second=ingest_catalyst_revision(**args)
    assert first[0]==second[0]
    assert second[1]=="UNCHANGED"
