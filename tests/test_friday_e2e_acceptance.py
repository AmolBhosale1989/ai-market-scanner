from dataclasses import replace

import pytest

from scanner.production_acceptance import CHAIN, deterministic_friday_session, validate_chain, ChainEvent


def _events():
    result=deterministic_friday_session()
    return [ChainEvent(**row) for row in result["chain"]]


def test_deterministic_friday_complete_chain_passes():
    result=deterministic_friday_session()
    assert result["status"]=="PASS"
    assert tuple(x["stage"] for x in result["chain"])==CHAIN
    assert len({x["production_run_id"] for x in result["chain"]})==1


def test_acceptance_rejects_provider_to_consumer_bypass():
    events=_events()
    events[3]=replace(events[3],input_hash="provider-bypass")
    with pytest.raises(RuntimeError,match="ACCEPTANCE_DEPENDENCY_FAILED"):
        validate_chain(events)


def test_acceptance_rejects_mixed_warehouse_snapshot():
    events=_events()
    events[5]=replace(events[5],production_run_id="other-run")
    with pytest.raises(RuntimeError,match="ACCEPTANCE_PROVENANCE_FAILED"):
        validate_chain(events)
