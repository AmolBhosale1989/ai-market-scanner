from __future__ import annotations

from datetime import datetime, timezone
import math

import pandas as pd

from .config import BENCHMARK, MIN_DATA_COVERAGE
from .data import download_batch, download_history
from .indicators import add_indicators
from .regime import evaluate_regime
from .stocks import analyze_dataframe


def _benchmark_context():
    hist = download_history(BENCHMARK, "6mo", "1d")
    if hist is None or len(hist) < 70:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: benchmark data unavailable.")
    d = add_indicators(hist)
    ret20 = float(d.iloc[-1]["RET20"])
    if not math.isfinite(ret20):
        raise RuntimeError("V3 LIVE REFRESH ABORTED: benchmark return invalid.")
    return ret20, evaluate_regime(hist)


def refresh_v3_candidates(base: pd.DataFrame) -> pd.DataFrame:
    """Refresh V3 candidate market/technical fields directly from the provider.

    The prior scan is used only to identify candidates and retain slow-moving
    context (company/catalyst/theme metadata). Price/technical decision fields
    are recomputed from a fresh provider download for the current run. If live
    provider coverage is unhealthy, fail closed instead of silently using old
    market values.
    """
    if base is None or base.empty or "ticker" not in base.columns:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: no candidate identities available.")

    tickers = list(dict.fromkeys(base["ticker"].dropna().astype(str)))
    if not tickers:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: candidate list is empty.")

    bench20, market_regime = _benchmark_context()
    histories = download_batch(tickers, period="1y", interval="1d")
    coverage = len(histories) / len(tickers)
    if coverage < MIN_DATA_COVERAGE:
        raise RuntimeError(
            "V3 LIVE REFRESH ABORTED: provider coverage below freshness contract: "
            f"{len(histories)}/{len(tickers)} ({coverage:.1%})."
        )

    refreshed = []
    for ticker in tickers:
        hist = histories.get(ticker)
        if hist is None or hist.empty:
            continue
        try:
            row = analyze_dataframe(
                ticker,
                hist,
                benchmark_return20=bench20,
                market_regime=market_regime,
            )
        except Exception as exc:
            print(f"V3 live refresh {ticker}: {exc}")
            continue
        if row:
            refreshed.append(row)

    if not refreshed:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: provider returned no analyzable candidates.")

    fresh = pd.DataFrame(refreshed)
    analyzable_coverage = fresh["ticker"].nunique() / len(tickers)
    if analyzable_coverage < MIN_DATA_COVERAGE:
        raise RuntimeError(
            "V3 LIVE REFRESH ABORTED: analyzable coverage below freshness contract: "
            f"{fresh['ticker'].nunique()}/{len(tickers)} ({analyzable_coverage:.1%})."
        )

    # Preserve slow-moving enrichment from the discovery scan, but never let it
    # overwrite freshly recomputed market/technical fields.
    slow = base.drop_duplicates("ticker").set_index("ticker")
    fresh = fresh.drop_duplicates("ticker").set_index("ticker")
    for col in slow.columns:
        if col not in fresh.columns:
            fresh[col] = slow[col]

    out = fresh.reset_index()
    out["v3_market_data_source"] = "DIRECT_PROVIDER"
    out["v3_market_data_refreshed_at_utc"] = datetime.now(timezone.utc).isoformat()
    out["v3_provider_coverage"] = round(coverage, 4)
    out["v3_analyzable_coverage"] = round(analyzable_coverage, 4)
    return out
