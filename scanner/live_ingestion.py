"""One bounded recovery fetch before the immutable warehouse gate snapshot."""
from datetime import datetime, timezone

from .control_plane import read_dataset
from .warehouse_gate import _symbols, build_tiers, coverage_frame, evaluate_tier
from .warehouse_refresh import refresh


def recover_required(tier):
    now = datetime.now(timezone.utc)
    frame = coverage_frame(tier, now)
    result = evaluate_tier(tier, frame, now_utc=now)
    if result['status'] == 'PASS':
        return
    # Samples are bounded diagnostic fields, not a complete recovery universe.
    # This strict tier contains only required benchmarks; inspect each to avoid
    # truncation and never broaden the provider request to the tradable universe.
    from dataclasses import replace
    targets = []
    for symbol in tier.symbols:
        single = replace(tier, symbols=(symbol,))
        subset = frame.loc[frame.ticker == symbol] if not frame.empty else frame
        if evaluate_tier(single, subset, now_utc=now)['status'] != 'PASS':
            targets.append(symbol)
    if targets:
        print('REQUIRED_INTRADAY_RECOVERY attempt=1 symbols=' + ','.join(targets), flush=True)
        refresh(targets, period='5d', interval='5m', benchmark_backfill=False,
                include_ingestion_dependencies=False)
    # The normal gate runs next at a new actual-time anchor, checks ALL tiers,
    # and blocks publication if this bounded retry did not repair the inputs.


def main():
    master = _symbols(read_dataset('master_universe'), 'master_universe')
    live = _symbols(read_dataset('live_universe'), 'live_universe')
    refresh(list(live), period='5d', interval='5m')
    tier = next(t for t in build_tiers(master, live) if t.name == 'CRITICAL_INTRADAY')
    recover_required(tier)


if __name__ == '__main__':
    main()
