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


def test_provider_requirements_support_different_freshness_slas():
    from datetime import timedelta
    from scanner.catalyst_contract import ProviderRequirement
    reqs=(ProviderRequirement("YAHOO_NEWS",timedelta(minutes=5)),
          ProviderRequirement("SEC_EDGAR",timedelta(minutes=15)),
          ProviderRequirement("EVENT_CALENDAR",timedelta(hours=12)))
    assert reqs[0].max_check_age < reqs[1].max_check_age < reqs[2].max_check_age


def test_future_domain_requires_explicit_opt_in(monkeypatch):
    from scanner import catalyst_warehouse as cw
    anchor=datetime(2026,9,25,15,0,tzinfo=timezone.utc)
    future=datetime(2026,9,26,15,0,tzinfo=timezone.utc)
    with pytest.raises(RuntimeError,match="CATALYST_LOOKAHEAD_BLOCKED"):
        cw.catalyst_context(tickers=("AAPL",),as_of=anchor,start_time=anchor,end_time=future)


def test_provider_domain_windows_are_asymmetric():
    from datetime import timedelta
    from scanner.catalyst_contract import ProviderRequirement
    anchor=datetime(2026,9,26,12,0,tzinfo=timezone.utc)
    sec=ProviderRequirement("SEC_EDGAR",timedelta(minutes=15),lookback=timedelta(hours=72))
    yahoo=ProviderRequirement("YAHOO_NEWS",timedelta(minutes=15),lookback=timedelta(hours=48))
    av=ProviderRequirement("ALPHA_VANTAGE",timedelta(hours=24),lookforward=timedelta(days=14),allow_future_domain=True)
    assert sec.window(anchor)==(anchor-timedelta(hours=72),anchor)
    assert yahoo.window(anchor)==(anchor-timedelta(hours=48),anchor)
    assert av.window(anchor)==(anchor,anchor+timedelta(days=14))
    assert sec.allow_future_domain is False and yahoo.allow_future_domain is False
    assert av.allow_future_domain is True


def test_existing_yahoo_bridge_uses_injected_anchor_and_quiet_is_clean():
    from scanner.catalyst_existing_adapters import ExistingYahooNewsAdapter
    class Delegate:
        def poll(self,frame,now=None):
            assert now==datetime(2026,9,26,12,0,tzinfo=timezone.utc)
            return [],{"errors":0}
    result=ExistingYahooNewsAdapter(Delegate()).fetch_catalysts(
        "AAPL",anchor=datetime(2026,9,26,12,0,tzinfo=timezone.utc))
    assert result.events==() and result.rejected_count==0


def test_existing_sec_bridge_fails_on_unresolved_identity():
    from scanner.catalyst_existing_adapters import ExistingSecEdgarAdapter
    class Delegate:
        def poll(self,frame,now=None):
            return [],{"errors":0,"unresolved":1}
    with pytest.raises(RuntimeError,match="SEC_EDGAR_PROVIDER_FAILED"):
        ExistingSecEdgarAdapter(Delegate()).fetch_catalysts(
            "AAPL",anchor=datetime(2026,9,26,12,0,tzinfo=timezone.utc))


def test_alpha_vantage_batch_fetches_once_and_indexes_future_window():
    from datetime import timedelta
    from scanner.catalyst_adapters import AlphaVantageCalendarBatch
    calls={"n":0}
    def fetch():
        calls["n"]+=1
        return "symbol,reportDate,fiscalDateEnding,estimate,currency\nAAPL,2026-09-30,2026-09-30,1.25,USD\n"
    batch=AlphaVantageCalendarBatch(fetch)
    anchor=datetime(2026,9,26,12,0,tzinfo=timezone.utc)
    indexed=batch.fetch_and_index(anchor=anchor,lookforward=timedelta(days=14))
    assert calls["n"]==1
    assert len(indexed["AAPL"].events)==1
    assert indexed["AAPL"].events[0].event_timestamp > anchor


def test_alpha_vantage_soft_error_never_becomes_quiet_market():
    from datetime import timedelta
    from scanner.catalyst_adapters import AlphaVantageCalendarBatch
    batch=AlphaVantageCalendarBatch(lambda:'{"Information":"rate limit"}')
    with pytest.raises(RuntimeError,match="SOFT_ERROR"):
        batch.fetch_and_index(anchor=datetime(2026,9,26,12,0,tzinfo=timezone.utc),
                              lookforward=timedelta(days=14))
