from datetime import datetime, timezone
import pytest

from scanner.catalyst_warehouse import _canonical_payload


def test_canonical_payload_hash_is_key_order_independent():
    a,ah=_canonical_payload({"b":2,"a":1})
    b,bh=_canonical_payload({"a":1,"b":2})
    assert a == b == '{"a":1,"b":2}'
    assert ah == bh and len(ah) == 64


def test_unmigrated_auxiliary_dataset_still_fails_closed(monkeypatch):
    from scanner import warehouse
    with pytest.raises(RuntimeError,match="WAREHOUSE_DATASET_NOT_MIGRATED"):
        warehouse.request_dataset("alternative_sentiment","test",tickers=("AAPL",))


def test_catalyst_dispatch_rejects_future_window(monkeypatch):
    from scanner import warehouse
    anchor=datetime(2026,9,25,15,0,tzinfo=timezone.utc)
    with pytest.raises(RuntimeError,match="WAREHOUSE_AUXILIARY_LOOKAHEAD"):
        warehouse.request_dataset("catalyst_context","test",tickers=("AAPL",),
            as_of=anchor,start_time=datetime(2026,9,25,14,0,tzinfo=timezone.utc),
            end_time=datetime(2026,9,25,15,1,tzinfo=timezone.utc))


def test_catalyst_states_are_explicit_strings():
    from scanner.catalyst_contract import CatalystState
    assert {x.value for x in CatalystState} == {"AVAILABLE","NO_EVENT","UNAVAILABLE","STALE"}
