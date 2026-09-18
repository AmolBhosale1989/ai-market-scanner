from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from .bitemporal_warehouse import PointInTimeRequirement, point_in_time, verify_health


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
    path: None
    rows: int
    frame: pd.DataFrame


def _tickers(values: Iterable[str] | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(x).upper() for x in (values or ()) if x))


def _pit(tickers: Iterable[str], interval: str, consumer: str) -> pd.DataFrame:
    wanted = _tickers(tickers)
    if not wanted:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {consumer} requested no tickers")
    return point_in_time(PointInTimeRequirement(
        consumer=consumer, tickers=wanted, data_type="OHLCV", timeframe=interval,
    ))


def _assert_coverage(df: pd.DataFrame, tickers: Iterable[str], consumer: str) -> None:
    wanted = set(_tickers(tickers))
    have = set(df["ticker"].astype(str).str.upper()) if not df.empty and "ticker" in df.columns else set()
    missing = sorted(wanted - have)
    if missing:
        sample = ",".join(missing[:10])
        raise RuntimeError(f"WAREHOUSE_COVERAGE_INCOMPLETE: {consumer} missing={sample} count={len(missing)}")


def _assert_fresh(df: pd.DataFrame, interval: str, max_age_minutes: int, consumer: str) -> pd.Timestamp:
    newest = pd.to_datetime(df["ingested_at"], utc=True, errors="coerce").max()
    if pd.isna(newest):
        raise RuntimeError(f"WAREHOUSE_STALE: {consumer} {interval} has no valid ingestion timestamp")
    age = (datetime.now(timezone.utc) - newest.to_pydatetime()).total_seconds() / 60
    if age < 0 or age > max_age_minutes:
        raise RuntimeError(f"WAREHOUSE_STALE: {consumer} {interval} age={age:.1f}m max={max_age_minutes}m")
    return newest


def _compat_frame(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["bar_timestamp"] = pd.to_datetime(x["event_timestamp"], utc=True, errors="coerce")
    rename = {"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
    x = x.rename(columns=rename)
    return x


def status() -> dict:
    health = verify_health()
    return {"status": health["status"], "backend": "POSTGRESQL_BITEMPORAL", "tls": health["tls"]}


def is_fresh(max_age_minutes: int = 20, interval: str | None = None) -> bool:
    # Database health is distinct from data freshness. Consumers validate their
    # requested rows through point-in-time reads; there is no CSV manifest.
    try:
        verify_health()
        return True
    except Exception:
        return False


def ensure(tickers: Iterable[str], period: str = "1y", interval: str = "1d", max_age_minutes: int = 20):
    """Compatibility preflight. Ingestion is a workflow responsibility, never a consumer side effect."""
    df = _pit(tickers, interval, "warehouse.ensure")
    _assert_coverage(df, tickers, "warehouse.ensure")
    newest = _assert_fresh(df, interval, max_age_minutes, "warehouse.ensure")
    return {"backend": "POSTGRESQL_BITEMPORAL", "updated_at_utc": newest.isoformat()}


def get(tickers: Iterable[str] | None = None, interval: str = "1d", max_age_minutes: int = 20, require_fresh: bool = True) -> pd.DataFrame:
    wanted = _tickers(tickers)
    if not wanted:
        raise RuntimeError("WAREHOUSE_REQUIREMENT_INVALID: PostgreSQL reads require explicit tickers")
    df = _compat_frame(_pit(wanted, interval, "warehouse.get"))
    _assert_coverage(df, wanted, "warehouse.get")
    if require_fresh:
        _assert_fresh(df, interval, max_age_minutes, "warehouse.get")
    return df


def request(tickers: Iterable[str], period: str = "1y", interval: str = "1d", max_age_minutes: int = 20) -> pd.DataFrame:
    ensure(tickers, period=period, interval=interval, max_age_minutes=max_age_minutes)
    return get(tickers, interval=interval, max_age_minutes=max_age_minutes)


def frames(tickers: Iterable[str], period: str = "1y", interval: str = "1d", max_age_minutes: int = 20) -> dict[str, pd.DataFrame]:
    df = request(tickers, period=period, interval=interval, max_age_minutes=max_age_minutes)
    out = {}
    for ticker, group in df.groupby("ticker"):
        x = group.copy().set_index("bar_timestamp")
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in x.columns]
        out[str(ticker)] = x[keep].sort_index()
    return out


def history(ticker: str, period: str = "1y", interval: str = "1d", max_age_minutes: int = 20) -> pd.DataFrame:
    return frames([ticker], period=period, interval=interval, max_age_minutes=max_age_minutes).get(str(ticker).upper(), pd.DataFrame())


def latest(tickers: Iterable[str] | None = None, interval: str = "1d", max_age_minutes: int = 20) -> pd.DataFrame:
    df = get(tickers=tickers, interval=interval, max_age_minutes=max_age_minutes)
    return df.sort_values("bar_timestamp").groupby("ticker", as_index=False).tail(1).reset_index(drop=True)


def provide(req: DataRequirement) -> WarehouseView:
    frame = request(req.tickers, period=req.period, interval=req.interval, max_age_minutes=req.max_age_minutes)
    if req.latest_only and not frame.empty:
        frame = frame.sort_values("bar_timestamp").groupby("ticker", as_index=False).tail(1)
    mandatory = ["ticker", "bar_timestamp", "provider", "ingested_at", "warehouse_run_id"]
    if req.columns:
        frame = frame[list(dict.fromkeys(mandatory + [x for x in req.columns if x in frame.columns]))].copy()
    newest = pd.to_datetime(frame["ingested_at"], utc=True, errors="coerce").max()
    run_id = str(frame["warehouse_run_id"].iloc[-1]) if not frame.empty else ""
    return WarehouseView(req.consumer, req.view_name or req.consumer, run_id,
                         newest.isoformat() if pd.notna(newest) else "", None, len(frame), frame.reset_index(drop=True))


def update(*args, **kwargs):
    raise RuntimeError("WAREHOUSE_UPDATE_REMOVED: PostgreSQL ingestion runs through scanner.warehouse_refresh")


def request_dataset(dataset: str, consumer: str, tickers: Iterable[str] | None = None, max_age_minutes: int = 60) -> pd.DataFrame:
    """Auxiliary datasets are Phase 4; fail closed rather than falling back to CSV/provider access."""
    raise RuntimeError(f"WAREHOUSE_DATASET_NOT_MIGRATED: {dataset} for {consumer}")
