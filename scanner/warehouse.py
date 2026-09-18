from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

from .config import OUTPUT_DIR
from .data import download_batch

WAREHOUSE_DIR = OUTPUT_DIR / "warehouse"
WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
MANIFEST = WAREHOUSE_DIR / "manifest.json"

@dataclass(frozen=True)
class DataRequirement:
    consumer: str
    tickers: tuple[str, ...]
    interval: str = "1d"
    period: str = "1y"
    max_age_minutes: int = 20
    columns: tuple[str, ...] = ()
    view_name: str = ""
    latest_only: bool = False

@dataclass(frozen=True)
class WarehouseView:
    consumer: str
    view_name: str
    run_id: str
    updated_at_utc: str
    path: Path
    rows: int
    frame: pd.DataFrame

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

def ensure(tickers: Iterable[str], period: str="1y", interval: str="1d", max_age_minutes: int=20) -> WarehouseSnapshot:
    """Warehouse-manager contract used by every scanner function.

    Consumers declare symbols/timeframe/freshness only. The manager decides
    whether the stored dataset is current and refreshes it from the provider
    when necessary. Provider access must remain inside this module/data.py.
    """
    tickers=list(dict.fromkeys(str(t).upper() for t in tickers if t))
    path=_dataset_path(interval)
    needs_refresh=not is_fresh(max_age_minutes=max_age_minutes,interval=interval)
    if not needs_refresh and path.exists():
        try:
            have=set(pd.read_csv(path,usecols=["ticker"])["ticker"].astype(str).str.upper())
            needs_refresh=not set(tickers).issubset(have)
        except Exception:
            needs_refresh=True
    if needs_refresh:
        return update(tickers,period=period,interval=interval)
    m=status()
    df=pd.read_csv(path,usecols=["ticker"])
    return WarehouseSnapshot(m["warehouse_run_id"],m["updated_at_utc"],interval,path,0,int(df["ticker"].nunique()))

def request(tickers: Iterable[str], period: str="1y", interval: str="1d", max_age_minutes: int=20) -> pd.DataFrame:
    """Declare a data requirement and receive a fresh warehouse-backed dataset."""
    tickers=list(dict.fromkeys(str(t).upper() for t in tickers if t))
    ensure(tickers,period=period,interval=interval,max_age_minutes=max_age_minutes)
    return get(tickers=tickers,interval=interval,max_age_minutes=max_age_minutes,require_fresh=True)

def frames(tickers: Iterable[str], period: str="1y", interval: str="1d", max_age_minutes: int=20) -> dict[str,pd.DataFrame]:
    """Compatibility shape for analytical functions that expect ticker->OHLCV."""
    df=request(tickers,period=period,interval=interval,max_age_minutes=max_age_minutes)
    out={}
    for ticker,g in df.groupby("ticker"):
        x=g.copy()
        x["bar_timestamp"]=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
        x=x.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")
        drop=[c for c in ("ticker","interval","provider","retrieved_at_utc","warehouse_run_id") if c in x.columns]
        out[str(ticker)]=x.drop(columns=drop)
    return out

def history(ticker: str, period: str="1y", interval: str="1d", max_age_minutes: int=20) -> pd.DataFrame:
    return frames([ticker],period=period,interval=interval,max_age_minutes=max_age_minutes).get(str(ticker).upper(),pd.DataFrame())

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


def provide(req: DataRequirement) -> WarehouseView:
    """Create a consumer-specific warehouse view from a declared requirement."""
    tickers=tuple(dict.fromkeys(str(t).upper() for t in req.tickers if t))
    if not tickers:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {req.consumer} requested no tickers")
    ensure(tickers,period=req.period,interval=req.interval,max_age_minutes=req.max_age_minutes)
    frame=get(tickers=tickers,interval=req.interval,max_age_minutes=req.max_age_minutes,require_fresh=True)
    frame["bar_timestamp"]=pd.to_datetime(frame["bar_timestamp"],utc=True,errors="coerce")
    if req.latest_only and not frame.empty:
        frame=frame.sort_values("bar_timestamp").groupby("ticker",as_index=False).tail(1)
    mandatory=["ticker","bar_timestamp","interval","provider","retrieved_at_utc","warehouse_run_id"]
    if req.columns:
        wanted=list(dict.fromkeys(mandatory+[x for x in req.columns if x in frame.columns]))
        frame=frame[wanted].copy()
    safe=(req.view_name or req.consumer).lower().replace(" ","_").replace("/","_")
    view_dir=WAREHOUSE_DIR/"views"
    view_dir.mkdir(parents=True,exist_ok=True)
    path=view_dir/f"{safe}.csv"
    tmp=path.with_suffix(".tmp")
    frame.to_csv(tmp,index=False)
    tmp.replace(path)
    m=status()
    catalog_path=WAREHOUSE_DIR/"view_catalog.csv"
    row=pd.DataFrame([{
        "consumer":req.consumer,"view_name":safe,"interval":req.interval,"period":req.period,
        "max_age_minutes":req.max_age_minutes,"latest_only":req.latest_only,
        "rows":len(frame),"symbols":frame["ticker"].nunique() if not frame.empty else 0,
        "warehouse_run_id":m.get("warehouse_run_id",""),"updated_at_utc":m.get("updated_at_utc",""),
        "view_path":str(path),
    }])
    if catalog_path.exists() and catalog_path.stat().st_size:
        old=pd.read_csv(catalog_path)
        old=old[~((old["consumer"]==req.consumer)&(old["view_name"]==safe))]
        row=pd.concat([old,row],ignore_index=True)
    row.to_csv(catalog_path,index=False)
    return WarehouseView(req.consumer,safe,m.get("warehouse_run_id",""),m.get("updated_at_utc",""),path,len(frame),frame.reset_index(drop=True))


def _aux_path(dataset: str) -> Path:
    return WAREHOUSE_DIR / f"{dataset}.csv"

def request_dataset(dataset: str, consumer: str, tickers: Iterable[str] | None=None, max_age_minutes: int=60) -> pd.DataFrame:
    """Single gateway for non-OHLCV datasets (news/events/options/profiles).

    Consumers never call providers. Ingestion jobs/manager populate these
    datasets. If unavailable or stale, this fails closed.
    """
    path=_aux_path(dataset)
    if not path.exists() or not path.stat().st_size:
        raise RuntimeError(f"WAREHOUSE_DATASET_UNAVAILABLE: {dataset} for {consumer}")
    df=pd.read_csv(path)
    ts_col=next((x for x in ("updated_at_utc","retrieved_at_utc","warehouse_updated_at_utc") if x in df.columns),None)
    if ts_col is None:
        raise RuntimeError(f"WAREHOUSE_DATASET_NO_TIMESTAMP: {dataset}")
    newest=pd.to_datetime(df[ts_col],utc=True,errors="coerce").max()
    if pd.isna(newest) or (_utc_now()-newest.to_pydatetime()).total_seconds()/60>max_age_minutes:
        raise RuntimeError(f"WAREHOUSE_DATASET_STALE: {dataset}")
    if tickers and "ticker" in df.columns:
        wanted={str(t).upper() for t in tickers}
        df=df[df["ticker"].astype(str).str.upper().isin(wanted)].copy()
    return df
