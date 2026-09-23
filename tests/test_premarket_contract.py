import pandas as pd


def test_premarket_reads_exactly_the_refreshed_live_universe(monkeypatch):
    import scanner.premarket as module

    reads = []
    batches = []
    written = {}

    def read_dataset(name):
        reads.append(name)
        return pd.DataFrame([
            {
                "ticker": "AAPL", "tradable": True, "price": 200.0,
                "avg_dollar_volume20": 1_000_000_000, "name": "Apple", "exchange": "NASDAQ",
            },
            {
                "ticker": "MSFT", "tradable": True, "price": 500.0,
                "avg_dollar_volume20": 900_000_000, "name": "Microsoft", "exchange": "NASDAQ",
            },
        ])

    def warehouse_batch(tickers, consumer, period="5d"):
        batches.append((tuple(tickers), consumer, period))
        return {}

    def write_dataset(name, frame, **_kwargs):
        written[name] = frame.copy()

    monkeypatch.setattr(module, "read_dataset", read_dataset)
    monkeypatch.setattr(module, "_warehouse_batch", warehouse_batch)
    monkeypatch.setattr(module, "write_dataset", write_dataset)

    result = module.run(batch_size=80)

    assert reads == ["live_universe"]
    assert batches == [(('AAPL', 'MSFT'), 'premarket', '1d')]
    assert result.empty
    assert written["premarket_health"].loc[0, "universe_source"] == "control-plane:live_universe"


def test_premarket_keeps_missing_live_symbol_fail_closed(monkeypatch):
    import scanner.premarket as module

    monkeypatch.setattr(module, "read_dataset", lambda _name: pd.DataFrame([
        {"ticker": "AAPL", "tradable": True, "price": 200.0, "avg_dollar_volume20": 1.0},
    ]))

    def missing(*_args, **_kwargs):
        raise RuntimeError("WAREHOUSE_COVERAGE_INCOMPLETE: premarket missing=AAPL count=1")

    monkeypatch.setattr(module, "_warehouse_batch", missing)

    try:
        module.run()
    except RuntimeError as exc:
        assert "PREMARKET_WAREHOUSE_BATCH_FAILED" in str(exc)
    else:
        raise AssertionError("missing live-universe data must block premarket publication")
