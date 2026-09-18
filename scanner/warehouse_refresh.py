from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .bitemporal_warehouse import finish_run, ingest_observations, start_run, verify_health
from .config import OUTPUT_DIR
from .data import download_batch


def _symbols() -> list[str]:
    paths = [OUTPUT_DIR / "tradable_universe.csv", Path("data/universe.csv")]
    for path in paths:
        if not path.exists() or not path.stat().st_size:
            continue
        df = pd.read_csv(path)
        for col in ("ticker", "symbol", "Ticker", "Symbol"):
            if col in df.columns:
                vals = df[col].dropna().astype(str).str.upper().str.strip()
                vals = [x for x in vals if x]
                if vals:
                    return list(dict.fromkeys(vals))
    raise RuntimeError("WAREHOUSE_REFRESH_FAILED: no universe catalogue available")


def _normalize(ticker: str, frame: pd.DataFrame, ingested_at: datetime) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy().reset_index()
    out = out.rename(columns={out.columns[0]: "event_timestamp"})
    out["event_timestamp"] = pd.to_datetime(out["event_timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["event_timestamp"])
    out.insert(0, "ticker", str(ticker).upper())
    out["ingested_at"] = ingested_at
    return out


def refresh(tickers: list[str], period: str = "1y", interval: str = "1d") -> dict:
    """Provider access is confined to ingestion; PostgreSQL is the only warehouse sink."""
    verify_health()
    tickers = list(dict.fromkeys(str(t).upper() for t in tickers if t))
    if not tickers:
        raise RuntimeError("WAREHOUSE_REFRESH_FAILED: no tickers requested")
    run_id = start_run(
        provider="YAHOO_YFINANCE",
        request_type="OHLCV_REFRESH",
        payload={"tickers": tickers, "period": period, "interval": interval},
    )
    try:
        batch = download_batch(tickers, period=period, interval=interval)
        ingested_at = datetime.now(timezone.utc)
        frames = [_normalize(t, batch.get(t), ingested_at) for t in tickers if t in batch]
        frames = [f for f in frames if not f.empty]
        if not frames:
            raise RuntimeError("WAREHOUSE_REFRESH_FAILED: provider returned no usable data")
        combined = pd.concat(frames, ignore_index=True, sort=False)
        ingest_observations(
            combined,
            run_id=run_id,
            provider="YAHOO_YFINANCE",
            data_type="OHLCV",
            timeframe=interval,
        )
        metadata = {
            "requested_symbols": len(tickers),
            "ingested_symbols": int(combined["ticker"].nunique()),
            "observations": int(len(combined)),
            "period": period,
            "interval": interval,
        }
        finish_run(run_id, "AVAILABLE", metadata=metadata)
        return {"run_id": run_id, **metadata}
    except Exception as exc:
        finish_run(run_id, "FAILED", error=str(exc))
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--period", default="1y")
    p.add_argument("--interval", default="1d")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    tickers = _symbols()
    if args.limit > 0:
        tickers = tickers[:args.limit]
    result = refresh(tickers, period=args.period, interval=args.interval)
    print(
        "BITEMPORAL_WAREHOUSE_AVAILABLE "
        f"run_id={result['run_id']} interval={result['interval']} "
        f"symbols={result['ingested_symbols']} observations={result['observations']}"
    )


if __name__ == "__main__":
    main()
