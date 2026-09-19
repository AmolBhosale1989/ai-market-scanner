from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .bitemporal_warehouse import finish_run, ingest_observations, latest_event_timestamps, start_run, verify_health
from .config import OUTPUT_DIR, RETRY_CHUNK_SIZE
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


def refresh(tickers: list[str], period: str = "5d", interval: str = "1d", bootstrap: bool = False) -> dict:
    """Provider access is confined to ingestion; PostgreSQL is the only warehouse sink."""
    verify_health()
    tickers = list(dict.fromkeys(str(t).upper() for t in tickers if t))
    # V3 market-regime discovery always requires SPY even when the tradable
    # universe catalogue excludes ETFs. Keep the benchmark in PostgreSQL.
    if "SPY" not in tickers:
        tickers.append("SPY")
    if not tickers:
        raise RuntimeError("WAREHOUSE_REFRESH_FAILED: no tickers requested")
    run_id = start_run(
        provider="YAHOO_YFINANCE",
        request_type="OHLCV_REFRESH",
        payload={"tickers": tickers, "period": period, "interval": interval},
    )
    try:
        watermarks = {} if bootstrap else latest_event_timestamps(tickers, timeframe=interval)
        effective_period = period if bootstrap else ("5d" if interval == "1d" else "2d")
        provider_chunk = min(10, RETRY_CHUNK_SIZE)
        ingested_symbols=set()
        observations=0
        # Download and commit each small provider batch immediately. A later
        # provider failure cannot discard already committed successful chunks.
        for i in range(0, len(tickers), provider_chunk):
            chunk=tickers[i:i+provider_chunk]
            batch=download_batch(chunk, period=effective_period, interval=interval)
            ingested_at=datetime.now(timezone.utc)
            frames=[]
            for t in chunk:
                if t not in batch:
                    continue
                frame=_normalize(t,batch.get(t),ingested_at)
                watermark=watermarks.get(t)
                if watermark is not None and not frame.empty:
                    wm=pd.Timestamp(watermark)
                    wm=wm.tz_localize("UTC") if wm.tzinfo is None else wm.tz_convert("UTC")
                    frame=frame[frame["event_timestamp"] > wm]
                if not frame.empty:
                    frames.append(frame)
            if not frames:
                continue
            combined=pd.concat(frames,ignore_index=True,sort=False)
            inserted=ingest_observations(combined,run_id=run_id,provider="YAHOO_YFINANCE",data_type="OHLCV",timeframe=interval)
            observations += inserted
            ingested_symbols.update(combined["ticker"].astype(str).str.upper().unique())
            print(f"WAREHOUSE_CHUNK_COMMITTED offset={i} requested={len(chunk)} symbols={combined['ticker'].nunique()} inserted={inserted}", flush=True)
        metadata={"requested_symbols":len(tickers),"ingested_symbols":len(ingested_symbols),
                  "observations":observations,"period":period,"interval":interval,
                  "mode":"BOOTSTRAP" if bootstrap else "INCREMENTAL"}
        if not ingested_symbols and bootstrap:
            raise RuntimeError("WAREHOUSE_REFRESH_FAILED: bootstrap provider returned no usable data")
        finish_run(run_id,"AVAILABLE",metadata=metadata)
        return {"run_id":run_id,**metadata}
    except Exception as exc:
        finish_run(run_id, "FAILED", error=str(exc))
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--period", default="5d")
    p.add_argument("--interval", default="1d")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--bootstrap", action="store_true")
    args = p.parse_args()
    tickers = _symbols()
    if args.offset > 0:
        tickers = tickers[args.offset:]
    if args.limit > 0:
        tickers = tickers[:args.limit]
    result = refresh(tickers, period=args.period, interval=args.interval, bootstrap=args.bootstrap)
    print(
        "BITEMPORAL_WAREHOUSE_AVAILABLE "
        f"run_id={result['run_id']} interval={result['interval']} "
        f"symbols={result['ingested_symbols']} observations={result['observations']}"
    )


if __name__ == "__main__":
    main()
