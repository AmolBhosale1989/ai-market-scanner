import time
import json
from datetime import datetime, timezone
from typing import Iterable
import pandas as pd
import yfinance as yf

from .config import BATCH_RETRIES, RETRY_CHUNK_SIZE, RETRY_BACKOFF_SECONDS
from .provider_diagnostics import describe_response

def _normalize_single(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out=df.copy()
    if isinstance(out.columns,pd.MultiIndex):
        # For a single ticker yfinance may return (Price, Ticker).
        out.columns=[c[0] for c in out.columns]
    return out.dropna(how="all").copy()

def _download_once(tickers, period, interval):
    if not tickers:
        return {}
    try:
        raw = yf.download(
            tickers=tickers,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
            timeout=30,
        )
    except TypeError:
        raw = yf.download(
            tickers=tickers,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            group_by="ticker",
            threads=True,
        )

    if raw is None or raw.empty:
        return {}

    out={}
    if len(tickers)==1:
        df=raw.copy()
        if isinstance(df.columns,pd.MultiIndex):
            # yfinance group_by="ticker" may return either (Ticker, Price)
            # or (Price, Ticker), depending on version/request shape.
            lvl0=set(map(str,df.columns.get_level_values(0)))
            lvl1=set(map(str,df.columns.get_level_values(1)))
            t=str(tickers[0])
            price_names={"Open","High","Low","Close","Adj Close","Volume"}
            if t in lvl0:
                df=df[t].copy()
            elif t in lvl1:
                df=df.xs(t,axis=1,level=1).copy()
            elif price_names.intersection(lvl0):
                df=df.copy()
                df.columns=[str(col[0]) for col in df.columns]
            elif price_names.intersection(lvl1):
                df=df.copy()
                df.columns=[str(col[1]) for col in df.columns]
            else:
                df=_normalize_single(df)
        else:
            df=_normalize_single(df)
        df=df.dropna(how="all")
        if not df.empty:
            out[tickers[0]]=df
        return out

    for t in tickers:
        try:
            if isinstance(raw.columns,pd.MultiIndex) and t in raw.columns.get_level_values(0):
                df=raw[t].copy().dropna(how="all")
            else:
                continue
            if not df.empty:
                out[t]=df
        except Exception:
            continue
    return out

def download_batch(
    tickers: Iterable[str],
    period: str = "1y",
    interval: str = "1d",
    retries: int = BATCH_RETRIES,
):
    """
    Download a batch and explicitly retry symbols missing from partial Yahoo
    responses. Partial failure is common under throttling and must not be
    mistaken for a successful batch.
    """
    tickers=list(dict.fromkeys(str(t) for t in tickers if t))
    if not tickers:
        return {}

    out={}
    remaining=tickers[:]

    for attempt in range(retries+1):
        if not remaining:
            break

        # Never send the full universe in one Yahoo request.  Large first
        # requests are the main source of throttling in CI, and retries cannot
        # recover before the live-core timeout once Yahoo has rate-limited the
        # runner.  Use the same bounded chunks on every attempt.
        chunks=[
            remaining[i:i+RETRY_CHUNK_SIZE]
            for i in range(0,len(remaining),RETRY_CHUNK_SIZE)
        ]

        for chunk in chunks:
            try:
                started = datetime.now(timezone.utc)
                clock = time.monotonic()
                got=_download_once(chunk,period,interval)
                received = datetime.now(timezone.utc)
                elapsed = time.monotonic() - clock
                for symbol in chunk:
                    diagnostic = describe_response(symbol, got.get(symbol), period=period,
                        interval=interval, started=started, received=received, elapsed=elapsed)
                    diagnostic['attempt'] = attempt + 1
                    print('PROVIDER_RESPONSE ' + json.dumps(diagnostic, allow_nan=False), flush=True)
                out.update(got)
            except Exception as e:
                print(f"Download retry {attempt+1} failed for {len(chunk)} symbols: {e}")

        remaining=[t for t in tickers if t not in out]
        if remaining and attempt<retries:
            sleep_for=RETRY_BACKOFF_SECONDS*(2**attempt)
            print(f"Retrying {len(remaining)} missing symbols after {sleep_for:.0f}s...")
            time.sleep(sleep_for)

    if remaining:
        print(f"Batch incomplete: {len(out)}/{len(tickers)} symbols returned; {len(remaining)} still missing")
    return out
