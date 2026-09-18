from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from .config import OUTPUT_DIR
from .data import download_batch

WAREHOUSE_DIR = OUTPUT_DIR / "warehouse"
WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = WAREHOUSE_DIR / "manifest.json"

@dataclass(frozen=True)
class WarehouseSnapshot:
    run_id: str
    updated_at_utc: str
    interval: str
    path: Path
    rows: int
    symbols: int

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _run_id(now: datetime | None = None) -> str:
    now = now or _utc_now()
    return now.strftime("%Y%m%dT%H%M%SZ")

def _dataset_path(interval: str) -> Path:
    safe = interval.replace("/", "_")
    return WAREHOUSE_DIR / f"market_{safe}.csv"

def _normalize_frame(ticker: str, df: pd.DataFrame, interval: str, retrieved_at: str, run_id: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x=df.copy().reset_index()
    time_col=x.columns[0]
    x=x.rename(columns={time_col:"bar_timestamp"})
    ts=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
    x["bar_timestamp"]=ts
    x=x.dropna(subset=["bar_timestamp"])
    x.insert(0,"ticker",str(ticker).upper())
    x["interval"]=interval
    x["provider"]="YAHOO_YFINANCE"
    x["retrieved_at_utc"]=retrieved_at
    x["warehouse_run_id"]=run_id
    return x

def _merge_existing(path: Path, fresh: pd.DataFrame) -> pd.DataFrame:
    if path.exists() and path.stat().st_size:
        old=pd.read_csv(path)
        combined=pd.concat([old,fresh],ignore_index=True,sort=False)
    else:
        combined=fresh.copy()
    if combined.empty:
        return combined
    combined["bar_timestamp"]=pd.to_datetime(combined["bar_timestamp"],utc=True,errors="coerce")
    combined=combined.dropna(subset=["bar_timestamp"])
    combined=combined.sort_values(["ticker","bar_timestamp","retrieved_at_utc"])
    combined=combined.drop_duplicates(["ticker","interval","bar_timestamp"],keep="last")
    return combined

def update(tickers: Iterable[str], period: str="1y", interval: str="1d") -> WarehouseSnapshot:
    tickers=list(dict.fromkeys(str(t).upper() for t in tickers if t))
    if not tickers:
        raise RuntimeError("WAREHOUSE_UPDATE_FAILED: no tickers requested")
    now=_utc_now()
    retrieved=now.isoformat(timespec="seconds")
    rid=_run_id(now)
    batch=download_batch(tickers,period=period,interval=interval)
    frames=[_normalize_frame(t,batch.get(t),interval,retrieved,rid) for t in tickers if t in batch]
    frames=[x for x in frames if not x.empty]
    if not frames:
        raise RuntimeError("WAREHOUSE_UPDATE_FAILED: provider returned no usable data")
    fresh=pd.concat(frames,ignore_index=True,sort=False)
    path=_dataset_path(interval)
    merged=_merge_existing(path,fresh)
    tmp=path.with_suffix(".tmp")
    merged.to_csv(tmp,index=False)
    tmp.replace(path)
    latest_bar=pd.to_datetime(fresh["bar_timestamp"],utc=True,errors="coerce").max()
    manifest={
        "status":"AVAILABLE",
        "warehouse_run_id":rid,
        "updated_at_utc":retrieved,
        "latest_bar_utc":latest_bar.isoformat() if pd.notna(latest_bar) else "",
        "interval":interval,
        "period":period,
        "provider":"YAHOO_YFINANCE",
        "requested_symbols":len(tickers),
        "updated_symbols":int(fresh["ticker"].nunique()),
        "rows_added_or_refreshed":int(len(fresh)),
        "dataset":str(path),
    }
    MANIFEST.write_text(json.dumps(manifest,indent=2))
    return WarehouseSnapshot(rid,retrieved,interval,path,len(merged),int(merged["ticker"].nunique()))

def status() -> dict:
    if not MANIFEST.exists():
        return {"status":"MISSING"}
    return json.loads(MANIFEST.read_text())

def is_fresh(max_age_minutes: int=20, interval: str | None=None) -> bool:
    m=status()
    if m.get("status")!="AVAILABLE":
        return False
    if interval and m.get("interval")!=interval:
        return False
    try:
        updated=pd.Timestamp(m["updated_at_utc"])
        if updated.tzinfo is None:
            updated=updated.tz_localize("UTC")
        age=(_utc_now()-updated.to_pydatetime()).total_seconds()/60
        return 0 <= age <= max_age_minutes
    except Exception:
        return False

def get(tickers: Iterable[str] | None=None, interval: str="1d", max_age_minutes: int=20, require_fresh: bool=True) -> pd.DataFrame:
    if require_fresh and not is_fresh(max_age_minutes=max_age_minutes,interval=interval):
        raise RuntimeError(f"WAREHOUSE_STALE: {interval} data is not current")
    path=_dataset_path(interval)
    if not path.exists():
        raise RuntimeError(f"WAREHOUSE_MISSING: {path.name}")
    df=pd.read_csv(path)
    if tickers:
        wanted={str(t).upper() for t in tickers}
        df=df[df["ticker"].astype(str).str.upper().isin(wanted)].copy()
    return df

def latest(tickers: Iterable[str] | None=None, interval: str="1d", max_age_minutes: int=20) -> pd.DataFrame:
    df=get(tickers=tickers,interval=interval,max_age_minutes=max_age_minutes,require_fresh=True)
    if df.empty:
        return df
    df["bar_timestamp"]=pd.to_datetime(df["bar_timestamp"],utc=True,errors="coerce")
    return df.sort_values("bar_timestamp").groupby("ticker",as_index=False).tail(1).reset_index(drop=True)
