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


def test_normalized_event_requires_identity_type_and_aware_time():
    from datetime import datetime,timezone
    from scanner.catalyst_adapters import NormalizedCatalystEvent,CatalystFetchResult
    good=NormalizedCatalystEvent("evt","NEWS",datetime(2026,9,25,tzinfo=timezone.utc),{"x":1})
    assert CatalystFetchResult((good,),rejected_count=2).rejected_count==2
    with pytest.raises(ValueError):
        NormalizedCatalystEvent("","NEWS",datetime(2026,9,25,tzinfo=timezone.utc),{})
    with pytest.raises(ValueError):
        NormalizedCatalystEvent("evt","NEWS",datetime(2026,9,25),{})
