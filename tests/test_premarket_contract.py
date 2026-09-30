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


def _intraday_rows(bars):
    return pd.DataFrame([
        dict(ticker=t, event_timestamp=pd.Timestamp(at),
             ingested_at=pd.Timestamp('2026-09-30T16:34:00Z'),
             warehouse_run_id='00000000-0000-0000-0000-000000000001',
             provider='TEST', open=100., high=101., low=99., close=100., volume=1000.)
        for t, at in bars.items()
    ])


def _freeze_rows(monkeypatch, rows, anchor='2026-09-30T16:35:45Z'):
    from scanner import warehouse, consumer_snapshot
    monkeypatch.setattr(warehouse, 'point_in_time', lambda req: rows.copy())
    monkeypatch.setattr(consumer_snapshot, 'consumer_anchor', lambda: pd.Timestamp(anchor))


def test_premarket_filters_stale_standard_symbol_before_calculation(monkeypatch):
    from scanner import premarket
    _freeze_rows(monkeypatch, _intraday_rows({
        'MKSI': '2026-09-30T16:20:00Z', 'SPY': '2026-09-30T16:30:00Z'}))
    result = premarket._warehouse_batch(['MKSI', 'SPY'], 'premarket', '1d')
    assert set(result) == {'SPY'}


def test_premarket_all_stale_standard_batch_writes_empty_output(monkeypatch, memory_control_plane):
    from scanner import premarket, control_plane
    _freeze_rows(monkeypatch, _intraday_rows({'MKSI': '2026-09-30T16:20:00Z'}))
    control_plane.write_dataset('live_universe', pd.DataFrame([
        dict(ticker='MKSI', price=100., avg_dollar_volume20=1000000.)]))
    result = premarket.run()
    assert result.empty and 'ticker' in result.columns
    assert control_plane.read_dataset('premarket_discovery').empty
    assert control_plane.read_dataset('premarket_health').iloc[0]['published_rows'] == 0


def test_premarket_stale_critical_symbols_remain_fatal(monkeypatch):
    import pytest
    from scanner import premarket
    critical = tuple(dict.fromkeys(premarket.CORE_INTRADAY_MARKET_SYMBOLS +
                                  premarket.ROTATION_REQUIRED_SYMBOLS))
    assert len(critical) == 21
    for symbol in critical:
        _freeze_rows(monkeypatch, _intraday_rows({symbol: '2026-09-30T16:20:00Z'}))
        with pytest.raises(RuntimeError, match='required_stale='+symbol):
            premarket._warehouse_batch([symbol], 'premarket', '1d')


def test_premarket_ten_minute_boundary_is_not_extended(monkeypatch):
    from scanner import premarket
    rows = _intraday_rows({'MKSI': '2026-09-30T16:20:00Z'})
    _freeze_rows(monkeypatch, rows, '2026-09-30T16:35:00Z')
    assert set(premarket._warehouse_batch(['MKSI'], 'premarket')) == {'MKSI'}
    _freeze_rows(monkeypatch, rows, '2026-09-30T16:35:01Z')
    assert premarket._warehouse_batch(['MKSI'], 'premarket') == {}


def test_empty_filter_option_does_not_hide_quality_failure(monkeypatch):
    import pytest
    from scanner import premarket
    rows = _intraday_rows({'MKSI': '2026-09-30T16:20:00Z'})
    rows['close'] = -1
    _freeze_rows(monkeypatch, rows)
    with pytest.raises(RuntimeError, match='WAREHOUSE_QUALITY_FAILED'):
        premarket._warehouse_batch(['MKSI'], 'premarket')
