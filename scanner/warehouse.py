from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pandas as pd
import pandas_market_calendars as mcal

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
    as_of: datetime | None = None
    min_bars_per_symbol: int = 1


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


def _pit(tickers: Iterable[str], interval: str, consumer: str, as_of: datetime | None = None) -> pd.DataFrame:
    wanted = _tickers(tickers)
    if not wanted:
        raise RuntimeError(f"WAREHOUSE_REQUIREMENT_INVALID: {consumer} requested no tickers")
    return point_in_time(PointInTimeRequirement(
        consumer=consumer, tickers=wanted, data_type="OHLCV", timeframe=interval, as_of=as_of,
    ))


def _assert_coverage(df: pd.DataFrame, tickers: Iterable[str], consumer: str) -> None:
    wanted = set(_tickers(tickers))
    have = set(df["ticker"].astype(str).str.upper()) if not df.empty and "ticker" in df.columns else set()
    missing = sorted(wanted - have)
    if missing:
        sample = ",".join(missing[:10])
        raise RuntimeError(f"WAREHOUSE_COVERAGE_INCOMPLETE: {consumer} missing={sample} count={len(missing)}")


def _freshness_failures(
    df: pd.DataFrame, interval: str, max_age_minutes: int, consumer: str
) -> tuple[list[str], pd.Timestamp, str]:
    """Return stale symbols using session-aware NYSE rules."""
    ingested = pd.to_datetime(df["ingested_at"], utc=True, errors="coerce").max()
    event_times = pd.to_datetime(df["event_timestamp"], utc=True, errors="coerce")
    newest_bar = event_times.max()
    if pd.isna(ingested) or pd.isna(newest_bar):
        raise RuntimeError(f"WAREHOUSE_STALE: {consumer} {interval} has no valid timestamp")

    now = pd.Timestamp.now(tz="UTC")
    cal = mcal.get_calendar("NYSE")
    schedule = cal.schedule(
        start_date=(now - pd.Timedelta(days=10)).date(),
        end_date=(now + pd.Timedelta(days=1)).date(),
    )
    if schedule.empty:
        raise RuntimeError(f"WAREHOUSE_STALE: {consumer} cannot resolve NYSE session")

    open_now = False
    current_open = current_close = None
    for _, row in schedule.iterrows():
        market_open = pd.Timestamp(row["market_open"]).tz_convert("UTC")
        market_close = pd.Timestamp(row["market_close"]).tz_convert("UTC")
        if market_open <= now <= market_close:
            open_now = True
            current_open, current_close = market_open, market_close
            break

    if interval != "1d" and open_now:
        # Intraday event timestamps label the start of the bar.  Freshness must
        # therefore be measured from the bar end; otherwise every five-minute
        # bar loses five minutes of its permitted age before it can be closed
        # and delivered by the provider.
        newest_by_symbol=(df.assign(_event=event_times).groupby("ticker")["_event"].max())
        try:
            bar_duration=pd.Timedelta(interval)
        except (TypeError,ValueError):
            bar_duration=pd.Timedelta(0)
        future_event=newest_by_symbol > now
        ages=((now-(newest_by_symbol+bar_duration)).dt.total_seconds()/60).clip(lower=0)
        stale=ages[future_event | (ages > max_age_minutes)]
        if not stale.empty:
            return stale.index.astype(str).tolist(), ingested, f"market_open bar_end_max={max_age_minutes}m"
        return [], ingested, f"market_open bar_end_max={max_age_minutes}m"

    # Closed market/weekend and daily bars: newest event must belong to the latest completed NYSE session.
    completed = schedule[pd.to_datetime(schedule["market_close"], utc=True) < now]
    if completed.empty:
        raise RuntimeError(f"WAREHOUSE_STALE: {consumer} {interval} has no completed NYSE session")
    latest_session = pd.Timestamp(completed.index[-1]).date()
    newest_by_symbol=(df.assign(_event=event_times).groupby("ticker")["_event"].max())
    if interval == "1d":
        newest_dates=newest_by_symbol.dt.date
    else:
        newest_dates=newest_by_symbol.dt.tz_convert("America/New_York").dt.date
    stale=newest_dates[newest_dates < latest_session]
    return stale.index.astype(str).tolist(), ingested, f"expected={latest_session}"


def _assert_fresh(df: pd.DataFrame, interval: str, max_age_minutes: int, consumer: str) -> pd.Timestamp:
    """Fail closed when any requested symbol violates the session freshness rule."""
    stale, ingested, expectation = _freshness_failures(df, interval, max_age_minutes, consumer)
    if stale:
        raise RuntimeError(
            f"WAREHOUSE_STALE: {consumer} {interval} stale_symbols={len(stale)} "
            f"sample={','.join(stale[:10])} {expectation}"
        )
    return ingested


def _quality_failures(df: pd.DataFrame, min_bars_per_symbol: int = 1) -> tuple[list[str], list[str]]:
    required=("ticker","event_timestamp","open","high","low","close","volume")
    missing=[c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"WAREHOUSE_QUALITY_FAILED: missing_columns={','.join(missing)}")
    x=df.copy()
    for c in ("open","high","low","close","volume"):
        x[c]=pd.to_numeric(x[c],errors="coerce")
    invalid=(
        x[["open","high","low","close"]].isna().any(axis=1)
        | x[["open","high","low","close"]].le(0).any(axis=1)
        | x["volume"].isna() | x["volume"].lt(0)
        | x["high"].lt(x[["open","close","low"]].max(axis=1))
        | x["low"].gt(x[["open","close","high"]].min(axis=1))
    )
    invalid_symbols=x.loc[invalid,"ticker"].astype(str).drop_duplicates().tolist()
    duplicate=x.duplicated(["ticker","event_timestamp"],keep=False)
    if duplicate.any():
        invalid_symbols=list(dict.fromkeys(invalid_symbols+x.loc[duplicate,"ticker"].astype(str).tolist()))
    counts=x.groupby("ticker").size()
    short=counts[counts < min_bars_per_symbol]
    return invalid_symbols,short.index.astype(str).tolist()


def _assert_quality(df: pd.DataFrame, consumer: str, min_bars_per_symbol: int = 1) -> None:
    invalid_symbols,short_symbols=_quality_failures(df,min_bars_per_symbol)
    if invalid_symbols:
        invalid_rows=int(df["ticker"].astype(str).isin(invalid_symbols).sum())
        raise RuntimeError(
            f"WAREHOUSE_QUALITY_FAILED: {consumer} invalid_rows={invalid_rows} "
            f"sample={','.join(invalid_symbols[:10])}"
        )
    if short_symbols:
        raise RuntimeError(
            f"WAREHOUSE_HISTORY_INCOMPLETE: {consumer} symbols={len(short_symbols)} "
            f"min_bars={min_bars_per_symbol} sample={','.join(short_symbols[:10])}"
        )


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
    raw = _pit(wanted, interval, "warehouse.get")
    _assert_quality(raw, "warehouse.get")
    df = _compat_frame(raw)
    _assert_coverage(df, wanted, "warehouse.get")
    if require_fresh:
        _assert_fresh(df, interval, max_age_minutes, "warehouse.get")
    return df


def request(tickers: Iterable[str], period: str = "1y", interval: str = "1d", max_age_minutes: int = 20) -> pd.DataFrame:
    ensure(tickers, period=period, interval=interval, max_age_minutes=max_age_minutes)
    return get(tickers, interval=interval, max_age_minutes=max_age_minutes)


def frames(tickers: Iterable[str], period: str = "1y", interval: str = "1d", max_age_minutes: int = 20, require_complete: bool = True) -> dict[str, pd.DataFrame]:
    # Discovery scans may tolerate provider-unavailable symbols; targeted consumers default fail-closed.
    wanted = _tickers(tickers)
    raw = _pit(wanted, interval, "warehouse.frames")
    if require_complete:
        _assert_quality(raw, "warehouse.frames")
    else:
        invalid,short=_quality_failures(raw)
        quarantined=set(invalid+short)
        if quarantined:
            raw=raw[~raw["ticker"].astype(str).isin(quarantined)].copy()
        if raw.empty:
            raise RuntimeError("WAREHOUSE_QUALITY_FAILED: warehouse.frames no usable symbols")
        stale,_,_=_freshness_failures(raw,interval,max_age_minutes,"warehouse.frames")
        if stale:
            raw=raw[~raw["ticker"].astype(str).isin(stale)].copy()
        if raw.empty:
            raise RuntimeError("WAREHOUSE_STALE: warehouse.frames no fresh symbols")
    df = _compat_frame(raw)
    if require_complete:
        _assert_coverage(df, wanted, "warehouse.frames")
    _assert_fresh(df, interval, max_age_minutes, "warehouse.frames")
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
    raw=_pit(req.tickers, req.interval, req.consumer, as_of=req.as_of)
    _assert_coverage(raw, req.tickers, req.consumer)
    _assert_quality(raw, req.consumer, min_bars_per_symbol=req.min_bars_per_symbol)
    _assert_fresh(raw, req.interval, req.max_age_minutes, req.consumer)
    frame=_compat_frame(raw)
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
