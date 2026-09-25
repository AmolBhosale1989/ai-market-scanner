from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd
import numpy as np

from .bitemporal_warehouse import _connect, finish_run, ingest_observations, latest_event_timestamps, start_run, verify_health
from .config import CRITICAL_MARKET_SYMBOLS, INGESTION_CRITICAL_SYMBOLS, RETRY_CHUNK_SIZE
from .control_plane import read_dataset
from .data import download_batch
from .warehouse import _freshness_failures
from .ohlcv_quality import invalid_rows
from .daily_repair import repair_daily
from .market_cutoff import completed_daily_session


def _symbols() -> list[str]:
    for name in ("master_universe", "tradable_universe"):
        df = read_dataset(name, required=False)
        if df.empty:
            continue
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
    out = frame.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(col[0]) for col in out.columns]
    out = out.reset_index()
    out = out.rename(columns={out.columns[0]: "event_timestamp"})
    required_ohlc = {"Open", "High", "Low", "Close"}
    if not required_ohlc.issubset(out.columns):
        raise RuntimeError(f"WAREHOUSE_REFRESH_FAILED: {ticker} missing OHLC columns")
    out["event_timestamp"] = pd.to_datetime(out["event_timestamp"], utc=True, errors="coerce")
    out = out.dropna(subset=["event_timestamp"])
    out.insert(0, "ticker", str(ticker).upper())
    out["ingested_at"] = ingested_at
    normalized=out.rename(columns={c:c.lower() for c in ("Open","High","Low","Close","Volume")})
    if "volume" not in normalized:
        raise RuntimeError(f"WAREHOUSE_REFRESH_FAILED: {ticker} missing Volume")
    bad=invalid_rows(normalized)
    if bad.any():
        print(f"WAREHOUSE_PROVIDER_REJECTED ticker={ticker} invalid_rows={int(bad.sum())}",flush=True)
    return out.loc[~bad].copy()


def _stale_watermarks(watermarks: dict[str, datetime], interval: str) -> set[str]:
    """Select only symbols whose newest stored event is behind the session freshness contract."""
    if not watermarks:
        return set()
    now=datetime.now(timezone.utc)
    frame=pd.DataFrame([
        {"ticker":ticker,"event_timestamp":event_at,"ingested_at":now}
        for ticker,event_at in watermarks.items()
    ])
    try:
        stale,_,_=_freshness_failures(frame,interval,10,"warehouse_refresh")
        return set(stale)
    except RuntimeError:
        # Calendar/timestamp uncertainty must fetch, never silently treat data as fresh.
        return set(watermarks)


def _refresh_candidates(watermarks: dict[str, datetime], interval: str, tickers: list[str]) -> set[str]:
    """Return existing symbols that must be fetched during this run.

    Every selected intraday symbol is always fetched. A full live
    refresh spans multiple provider batches, so a symbol that is fresh
    at job start can otherwise exceed its consumer SLA before the gate runs.
    """
    candidates=_stale_watermarks(watermarks,interval)
    if interval != "1d":
        candidates.update(set(tickers).intersection(watermarks))
    return candidates


def _completed_observations(frame, interval, now):
    """Keep observed closed candles; never freeze an unfinished provider bar."""
    if frame.empty:
        return frame
    events = pd.to_datetime(frame.event_timestamp, utc=True)
    if interval == "1d":
        return frame.loc[events.dt.date <= completed_daily_session()].copy()
    return frame.loc[events + pd.Timedelta(interval) <= pd.Timestamp(now)].copy()


def refresh(tickers: list[str], period: str = "5d", interval: str = "1d", bootstrap: bool = False, benchmark_backfill: bool = True, include_ingestion_dependencies: bool = True) -> dict:
    """Provider access is confined to ingestion; PostgreSQL is the only warehouse sink."""
    verify_health()
    tickers = list(dict.fromkeys(str(t).upper() for t in tickers if t))
    # V3 market-regime discovery always requires SPY even when the tradable
    # universe catalogue excludes ETFs. Keep the benchmark in PostgreSQL.
    # The bounded critical preflight supplies its complete 30-symbol contract.
    # Broad daily/live ingestion still adds every dependency by default.
    if include_ingestion_dependencies:
        tickers = list(dict.fromkeys(tickers + list(INGESTION_CRITICAL_SYMBOLS)))
    # V3 needs >=70 daily benchmark observations for regime/RET20. A benchmark
    # introduced after the universe bootstrap must be backfilled once, not left
    # with only the incremental 5-day window.
    if interval in {"1d", "5m"} and not bootstrap and benchmark_backfill:
        benchmark_period = "1y" if interval == "1d" else "5d"
        min_benchmark_rows = 70 if interval == "1d" else 150
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT count(*) FROM market_observation o
                JOIN instrument i ON i.instrument_id=o.instrument_id
                WHERE i.canonical_symbol='SPY' AND o.data_type='OHLCV' AND o.timeframe=%s""", (interval,))
            spy_rows = int(cur.fetchone()[0])
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT count(*) FROM market_observation o
                JOIN instrument i ON i.instrument_id=o.instrument_id
                WHERE i.canonical_symbol='SPY' AND o.data_type='OHLCV' AND o.timeframe=%s
                  AND (o.open IS NULL OR o.high IS NULL OR o.low IS NULL OR o.close IS NULL)""", (interval,))
            spy_malformed = int(cur.fetchone()[0])
        if spy_rows < min_benchmark_rows or spy_malformed > 0:
            benchmark_run = refresh(["SPY"], period=benchmark_period, interval=interval, bootstrap=True, benchmark_backfill=False, include_ingestion_dependencies=False)
            print(f"WAREHOUSE_BENCHMARK_BACKFILLED rows_before={spy_rows} malformed_before={spy_malformed} inserted={benchmark_run['observations']}", flush=True)
    if not tickers:
        raise RuntimeError("WAREHOUSE_REFRESH_FAILED: no tickers requested")
    run_id = start_run(
        provider="YAHOO_YFINANCE",
        request_type="OHLCV_REFRESH",
        payload={"tickers": tickers, "period": period, "interval": interval},
    )
    try:
        watermarks = {} if bootstrap else latest_event_timestamps(tickers, timeframe=interval)
        stale_existing=set(watermarks) if bootstrap else _refresh_candidates(watermarks,interval,tickers)
        incremental_period = "5d" if interval == "1d" else "2d"
        # Match the provider retry chunk so each network response and database
        # transaction carries a useful batch without returning to unsafe
        # full-universe requests.
        provider_chunk = RETRY_CHUNK_SIZE
        ingested_symbols=set()
        observations=0
        # Download and commit each small provider batch immediately. A later
        # provider failure cannot discard already committed successful chunks.
        for i in range(0, len(tickers), provider_chunk):
            chunk=tickers[i:i+provider_chunk]
            # A newly listed/missed symbol must receive the requested history;
            # existing symbols use the bounded incremental window.
            existing=[t for t in chunk if t in watermarks and t in stale_existing]
            missing=[t for t in chunk if t not in watermarks]
            batch={}
            if existing:
                batch.update(download_batch(existing, period=incremental_period, interval=interval))
            if missing:
                batch.update(download_batch(missing, period=period, interval=interval))
            if interval == "1d":
                # Recovery stays in ingestion. Retain the observed 5m inputs in
                # PostgreSQL before publishing any reconstructed daily candle.
                damaged=[t for t,f in batch.items() if not f.empty and
                         invalid_rows(f.rename(columns={c:c.lower() for c in
                             ("Open","High","Low","Close","Volume")})).any()]
                for recovery_interval in ("5m","30m","60m"):
                    if not damaged:
                        break
                    recovery=download_batch(damaged,period="5d",interval=recovery_interval)
                    for t,bars in recovery.items():
                        fixed=repair_daily(batch[t],bars,interval=recovery_interval)
                        if "daily_bar_source" not in fixed:
                            continue
                        source=_normalize(t,bars,datetime.now(timezone.utc))
                        evidence=fixed.dropna(subset=["daily_bar_source"]).iloc[-1]
                        source=source[source.event_timestamp.between(
                            pd.Timestamp(evidence.source_first_bar_utc),
                            pd.Timestamp(evidence.source_last_bar_utc))]
                        ingest_observations(source,run_id=run_id,provider="YAHOO_YFINANCE",
                                            data_type="OHLCV",timeframe=recovery_interval)
                        batch[t]=fixed
                        damaged.remove(t)
                        print(f"WAREHOUSE_DAILY_RECONSTRUCTED ticker={t} source=complete_{recovery_interval}_session",flush=True)
            ingested_at=datetime.now(timezone.utc)
            frames=[]
            for t in chunk:
                if t not in batch:
                    continue
                frame=_normalize(t,batch.get(t),ingested_at)
                frame=_completed_observations(frame, interval, ingested_at)
                watermark=watermarks.get(t)
                if watermark is not None and not frame.empty:
                    wm=pd.Timestamp(watermark)
                    wm=wm.tz_localize("UTC") if wm.tzinfo is None else wm.tz_convert("UTC")
                    frame=frame[frame["event_timestamp"] >= wm]
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
                  "mode":"BOOTSTRAP" if bootstrap else "INCREMENTAL_WITH_MISSING_BACKFILL",
                  "preexisting_symbols":len(watermarks),
                  "missing_symbols_backfilled":len(set(tickers)-set(watermarks))}
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
    p.add_argument("--critical-only",action="store_true",help="Preflight required benchmarks before broad ingestion")
    p.add_argument("--dataset",default="",help="Current-run PostgreSQL dataset containing symbols")
    p.add_argument("--sample",type=int,default=0,help="Deterministic representative sample before adding critical symbols")
    args = p.parse_args()
    if args.critical_only:
        tickers=list(CRITICAL_MARKET_SYMBOLS)
    elif args.dataset:
        frame=read_dataset(args.dataset)
        col=next((c for c in ("ticker","symbol","Ticker","Symbol") if c in frame.columns),None)
        if not col:
            raise RuntimeError(f"WAREHOUSE_REFRESH_FAILED: no symbol column in {args.dataset}")
        tickers=list(dict.fromkeys(frame[col].dropna().astype(str).str.upper().str.strip()))
    else:
        tickers = _symbols()
    if args.sample > 0 and args.sample < len(tickers):
        idx=np.linspace(0,len(tickers)-1,num=args.sample,dtype=int)
        tickers=[tickers[i] for i in idx]
    if args.offset > 0:
        tickers = tickers[args.offset:]
    if args.limit > 0:
        tickers = tickers[:args.limit]
    result = refresh(tickers, period=args.period, interval=args.interval, bootstrap=args.bootstrap,
                     include_ingestion_dependencies=not args.critical_only)
    print(
        "BITEMPORAL_WAREHOUSE_AVAILABLE "
        f"run_id={result['run_id']} interval={result['interval']} "
        f"symbols={result['ingested_symbols']} observations={result['observations']}"
    )


if __name__ == "__main__":
    main()
