from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Any, Mapping, Protocol

import pandas as pd
import yfinance as yf

from .contracts import EventType, MarketEvent
from ..warehouse import history as warehouse_history


@dataclass(frozen=True)
class EvidencePollResult:
    events: list[MarketEvent]
    frame: pd.DataFrame
    health: dict[str, Any]


class OptionsMicrostructureAdapter(Protocol):
    def poll(self, candidates: pd.DataFrame) -> EvidencePollResult: ...


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sum_numeric(frame: pd.DataFrame, column: str) -> float:
    if frame is None or frame.empty or column not in frame.columns:
        return 0.0
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def _weighted_mean(frame: pd.DataFrame, value_col: str, weight_col: str) -> float | None:
    if frame is None or frame.empty or value_col not in frame.columns or weight_col not in frame.columns:
        return None
    values = pd.to_numeric(frame[value_col], errors="coerce")
    weights = pd.to_numeric(frame[weight_col], errors="coerce").fillna(0)
    mask = values.notna() & weights.gt(0)
    if not mask.any():
        return None
    total = float(weights[mask].sum())
    return float((values[mask] * weights[mask]).sum() / total) if total > 0 else None


class YahooOptionsMicrostructureAdapter:
    """Free shadow adapter for bounded options and bar-derived microstructure evidence.

    This is research evidence only. Yahoo does not provide a licensed consolidated
    options-flow feed or full depth-of-book here, so V4.4 never treats these fields
    as execution-grade confirmation. Missing evidence is explicit and non-fatal.
    """

    provider = "yahoo-shadow"

    def __init__(
        self,
        max_workers: int = 4,
        max_tickers: int = 8,
        min_contract_volume: int = 100,
        unusual_volume_oi_ratio: float = 2.0,
    ):
        self.max_workers = max(1, min(int(max_workers), 8))
        self.max_tickers = max(0, int(max_tickers))
        self.min_contract_volume = max(1, int(min_contract_volume))
        self.unusual_volume_oi_ratio = max(float(unusual_volume_oi_ratio), 0.1)

    def _options_snapshot(self, ticker: yf.Ticker) -> dict[str, Any]:
        expirations = list(ticker.options or [])
        if not expirations:
            return {
                "options_status": "NO_CHAIN",
                "option_expiry": "",
                "call_volume": None,
                "put_volume": None,
                "call_put_volume_ratio": None,
                "call_open_interest": None,
                "put_open_interest": None,
                "unusual_call_contracts": 0,
                "unusual_put_contracts": 0,
                "call_implied_volatility": None,
                "put_implied_volatility": None,
            }

        expiry = expirations[0]
        chain = ticker.option_chain(expiry)
        calls = chain.calls.copy() if chain.calls is not None else pd.DataFrame()
        puts = chain.puts.copy() if chain.puts is not None else pd.DataFrame()

        call_volume = _sum_numeric(calls, "volume")
        put_volume = _sum_numeric(puts, "volume")
        call_oi = _sum_numeric(calls, "openInterest")
        put_oi = _sum_numeric(puts, "openInterest")

        def unusual_count(frame: pd.DataFrame) -> int:
            if frame is None or frame.empty:
                return 0
            volume = pd.to_numeric(frame.get("volume"), errors="coerce").fillna(0)
            oi = pd.to_numeric(frame.get("openInterest"), errors="coerce").fillna(0)
            ratio = volume / oi.clip(lower=1)
            return int((volume.ge(self.min_contract_volume) & ratio.ge(self.unusual_volume_oi_ratio)).sum())

        return {
            "options_status": "OK",
            "option_expiry": str(expiry),
            "call_volume": round(call_volume, 2),
            "put_volume": round(put_volume, 2),
            "call_put_volume_ratio": round(call_volume / put_volume, 3) if put_volume > 0 else None,
            "call_open_interest": round(call_oi, 2),
            "put_open_interest": round(put_oi, 2),
            "unusual_call_contracts": unusual_count(calls),
            "unusual_put_contracts": unusual_count(puts),
            "call_implied_volatility": _weighted_mean(calls, "impliedVolatility", "volume"),
            "put_implied_volatility": _weighted_mean(puts, "impliedVolatility", "volume"),
        }

    @staticmethod
    def _microstructure_snapshot(bars: pd.DataFrame) -> dict[str, Any]:
        if bars is None or bars.empty:
            return {
                "microstructure_status": "NO_BARS",
                "bar_count": 0,
                "volume_acceleration_5m": None,
                "price_pressure_5m_pct": None,
                "last_bar_close_location": None,
                "last_bar_dollar_volume": None,
            }

        bars = bars.dropna(subset=["Close"]).copy()
        if bars.empty:
            return {
                "microstructure_status": "NO_BARS",
                "bar_count": 0,
                "volume_acceleration_5m": None,
                "price_pressure_5m_pct": None,
                "last_bar_close_location": None,
                "last_bar_dollar_volume": None,
            }

        volume = pd.to_numeric(bars.get("Volume"), errors="coerce").fillna(0)
        close = pd.to_numeric(bars["Close"], errors="coerce")
        latest = bars.iloc[-1]
        last_close = _number(latest.get("Close"))
        last_high = _number(latest.get("High"))
        last_low = _number(latest.get("Low"))
        last_volume = _number(latest.get("Volume"))

        recent5 = volume.tail(5)
        prior = volume.iloc[:-5].tail(20)
        recent_rate = float(recent5.mean()) if len(recent5) else 0.0
        prior_rate = float(prior.mean()) if len(prior) else 0.0
        acceleration = recent_rate / prior_rate if prior_rate > 0 else None

        pressure = None
        if len(close.dropna()) >= 6:
            start = float(close.dropna().iloc[-6])
            end = float(close.dropna().iloc[-1])
            pressure = ((end / start) - 1.0) * 100 if start else None

        close_location = None
        if last_close is not None and last_high is not None and last_low is not None and last_high > last_low:
            close_location = (last_close - last_low) / (last_high - last_low)

        dollar_volume = (
            last_close * last_volume
            if last_close is not None and last_volume is not None
            else None
        )
        return {
            "microstructure_status": "OK",
            "bar_count": int(len(bars)),
            "volume_acceleration_5m": round(acceleration, 3) if acceleration is not None else None,
            "price_pressure_5m_pct": round(pressure, 3) if pressure is not None else None,
            "last_bar_close_location": round(close_location, 3) if close_location is not None else None,
            "last_bar_dollar_volume": round(dollar_volume, 2) if dollar_volume is not None else None,
        }

    def _fetch_ticker(self, symbol: str) -> dict[str, Any]:
        started = time.monotonic()
        ticker = yf.Ticker(symbol)
        options_error = ""
        micro_error = ""
        try:
            options = self._options_snapshot(ticker)
        except Exception as exc:
            options_error = type(exc).__name__
            options = {
                "options_status": "ERROR",
                "option_expiry": "",
                "call_volume": None,
                "put_volume": None,
                "call_put_volume_ratio": None,
                "call_open_interest": None,
                "put_open_interest": None,
                "unusual_call_contracts": 0,
                "unusual_put_contracts": 0,
                "call_implied_volatility": None,
                "put_implied_volatility": None,
            }
        try:
            bars = warehouse_history(symbol, period="1d", interval="5m", max_age_minutes=15)
            micro = self._microstructure_snapshot(bars)
        except Exception as exc:
            micro_error = type(exc).__name__
            micro = {
                "microstructure_status": "ERROR",
                "bar_count": 0,
                "volume_acceleration_5m": None,
                "price_pressure_5m_pct": None,
                "last_bar_close_location": None,
                "last_bar_dollar_volume": None,
            }

        return {
            "ticker": symbol,
            **options,
            **micro,
            "options_error": options_error,
            "microstructure_error": micro_error,
            "provider_duration_ms": round((time.monotonic() - started) * 1000),
        }

    @staticmethod
    def _event_payload(row: Mapping[str, Any], keys: list[str]) -> dict[str, Any]:
        return {key: row.get(key) for key in keys}

    def poll(self, candidates: pd.DataFrame) -> EvidencePollResult:
        started = datetime.now(timezone.utc)
        if candidates is None or candidates.empty or self.max_tickers <= 0:
            health = {
                "provider": self.provider,
                "status": "SKIPPED",
                "requested": 0,
                "received": 0,
                "errors": 0,
                "options_available": 0,
                "microstructure_available": 0,
                "started_at_utc": started.isoformat(),
                "completed_at_utc": started.isoformat(),
            }
            return EvidencePollResult([], pd.DataFrame(), health)

        symbols = (
            candidates.get("ticker", pd.Series(dtype=str))
            .astype(str)
            .str.strip()
            .str.upper()
            .replace("", pd.NA)
            .dropna()
            .drop_duplicates()
            .head(self.max_tickers)
            .tolist()
        )

        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(symbols)) or 1) as pool:
            futures = {pool.submit(self._fetch_ticker, symbol): symbol for symbol in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    rows.append(future.result())
                except Exception as exc:
                    rows.append({
                        "ticker": symbol,
                        "options_status": "ERROR",
                        "microstructure_status": "ERROR",
                        "options_error": type(exc).__name__,
                        "microstructure_error": type(exc).__name__,
                        "provider_duration_ms": 0,
                    })

        frame = pd.DataFrame(rows)
        if not frame.empty:
            order = {symbol: index for index, symbol in enumerate(symbols)}
            frame["_order"] = frame["ticker"].map(order)
            frame = frame.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)

        observed = datetime.now(timezone.utc).isoformat()
        events: list[MarketEvent] = []
        options_keys = [
            "options_status", "option_expiry", "call_volume", "put_volume",
            "call_put_volume_ratio", "call_open_interest", "put_open_interest",
            "unusual_call_contracts", "unusual_put_contracts",
            "call_implied_volatility", "put_implied_volatility", "options_error",
        ]
        micro_keys = [
            "microstructure_status", "bar_count", "volume_acceleration_5m",
            "price_pressure_5m_pct", "last_bar_close_location",
            "last_bar_dollar_volume", "microstructure_error",
        ]
        for row in frame.to_dict(orient="records"):
            ticker = str(row["ticker"])
            events.append(MarketEvent(
                event_type=EventType.OPTIONS_FLOW,
                ticker=ticker,
                observed_at_utc=observed,
                source=self.provider,
                payload=self._event_payload(row, options_keys),
            ))
            events.append(MarketEvent(
                event_type=EventType.MICROSTRUCTURE,
                ticker=ticker,
                observed_at_utc=observed,
                source=self.provider,
                payload=self._event_payload(row, micro_keys),
            ))

        options_available = int(frame.get("options_status", pd.Series(dtype=str)).eq("OK").sum())
        micro_available = int(frame.get("microstructure_status", pd.Series(dtype=str)).eq("OK").sum())
        errors = int(
            frame.get("options_status", pd.Series(dtype=str)).eq("ERROR").sum()
            + frame.get("microstructure_status", pd.Series(dtype=str)).eq("ERROR").sum()
        )
        received = int(
            (
                frame.get("options_status", pd.Series(dtype=str)).isin(["OK", "NO_CHAIN"])
                | frame.get("microstructure_status", pd.Series(dtype=str)).eq("OK")
            ).sum()
        )
        completed = datetime.now(timezone.utc)
        health = {
            "provider": self.provider,
            "status": "OK" if errors == 0 else ("DEGRADED" if received else "FAILED"),
            "requested": len(symbols),
            "received": received,
            "errors": errors,
            "options_available": options_available,
            "microstructure_available": micro_available,
            "missing_data_is_nonfatal": True,
            "execution_grade": False,
            "started_at_utc": started.isoformat(),
            "completed_at_utc": completed.isoformat(),
            "duration_ms": round((completed - started).total_seconds() * 1000),
        }
        return EvidencePollResult(events, frame, health)
