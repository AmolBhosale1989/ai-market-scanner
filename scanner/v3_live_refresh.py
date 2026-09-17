from __future__ import annotations

from datetime import datetime, timezone
import math

import pandas as pd

from .config import BENCHMARK, MIN_DATA_COVERAGE
from .data import download_batch, download_history
from .indicators import add_indicators
from .regime import evaluate_regime
from .stocks import analyze_dataframe


REQUIRED_V3_LIVE_COLUMNS = {
    "ticker", "price", "stage", "technical_score", "risk_score",
    "entry_trigger", "stop", "effective_target", "effective_rr",
    "rvol", "avg_dollar_volume", "market_regime_state",
}


def _benchmark_context():
    hist = download_history(BENCHMARK, "6mo", "1d")
    if hist is None or len(hist) < 70:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: benchmark data unavailable.")
    d = add_indicators(hist)
    ret20 = float(d.iloc[-1]["RET20"])
    if not math.isfinite(ret20):
        raise RuntimeError("V3 LIVE REFRESH ABORTED: benchmark return invalid.")
    return ret20, evaluate_regime(hist)


def _validate_schema(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_V3_LIVE_COLUMNS - set(frame.columns))
    if missing:
        raise RuntimeError("V3_INPUT_SCHEMA_FAILED: " + ",".join(missing))
    if frame["ticker"].isna().any():
        raise RuntimeError("V3_INPUT_SCHEMA_FAILED: ticker contains null values")


def _build_v3_rank(frame: pd.DataFrame) -> pd.Series:
    technical = pd.to_numeric(frame["technical_score"], errors="coerce").fillna(0).clip(0, 100)
    risk = pd.to_numeric(frame["risk_score"], errors="coerce").fillna(100).clip(0, 100)
    if "broad_breakout_score" in frame.columns:
        discovery = pd.to_numeric(frame["broad_breakout_score"], errors="coerce").fillna(0).clip(0, 100)
    elif "rotation_leader_score" in frame.columns:
        discovery = pd.to_numeric(frame["rotation_leader_score"], errors="coerce").fillna(0).clip(0, 100)
    else:
        discovery = pd.Series(0.0, index=frame.index)
    rr = pd.to_numeric(frame["effective_rr"], errors="coerce").fillna(0).clip(0, 4) / 4 * 100
    score = technical * 0.55 + (100 - risk) * 0.20 + rr * 0.15 + discovery * 0.10
    return score.clip(0, 100).round(1)


def refresh_v3_candidates(base: pd.DataFrame) -> pd.DataFrame:
    if base is None or base.empty or "ticker" not in base.columns:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: no fresh candidate identities available.")

    tickers = list(dict.fromkeys(base["ticker"].dropna().astype(str)))
    if not tickers:
        raise RuntimeError("V3 LIVE REFRESH ABORTED: fresh candidate list is empty.")

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
            row = analyze_dataframe(ticker, hist, benchmark_return20=bench20, market_regime=market_regime)
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

    discovery = base.drop_duplicates("ticker").set_index("ticker")
    fresh = fresh.drop_duplicates("ticker").set_index("ticker")
    for col in discovery.columns:
        if col not in fresh.columns:
            fresh[col] = discovery[col]

    out = fresh.reset_index()
    _validate_schema(out)
    out["market_hunt_score"] = _build_v3_rank(out)
    out["v3_rank_source"] = "V3_NATIVE_DIRECT_PROVIDER"
    out["v3_market_data_source"] = "DIRECT_PROVIDER"
    out["v3_market_data_refreshed_at_utc"] = datetime.now(timezone.utc).isoformat()
    out["v3_provider_coverage"] = round(coverage, 4)
    out["v3_analyzable_coverage"] = round(analyzable_coverage, 4)
    return out
