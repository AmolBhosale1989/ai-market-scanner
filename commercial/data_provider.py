from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class MarketDataReadiness:
    provider: str
    licensed_for_commercial_use: bool
    real_time: bool
    historical: bool
    options: bool
    news: bool

    @property
    def commercial_ready(self) -> bool:
        return bool(self.licensed_for_commercial_use and self.real_time and self.historical)


def current_market_data_readiness() -> MarketDataReadiness:
    """
    Commercial readiness gate. The current Yahoo/yfinance development source is
    deliberately never marked commercially ready. A future licensed provider
    must be explicitly configured via environment variables.
    """
    provider=os.getenv("MARKET_DATA_PROVIDER","YAHOO_DEV").strip().upper() or "YAHOO_DEV"
    if provider=="YAHOO_DEV":
        return MarketDataReadiness(
            provider=provider,
            licensed_for_commercial_use=False,
            real_time=False,
            historical=True,
            options=False,
            news=False,
        )

    return MarketDataReadiness(
        provider=provider,
        licensed_for_commercial_use=os.getenv("MARKET_DATA_COMMERCIAL_LICENSED","").lower() in {"1","true","yes"},
        real_time=os.getenv("MARKET_DATA_REALTIME","").lower() in {"1","true","yes"},
        historical=os.getenv("MARKET_DATA_HISTORICAL","true").lower() in {"1","true","yes"},
        options=os.getenv("MARKET_DATA_OPTIONS","").lower() in {"1","true","yes"},
        news=os.getenv("MARKET_DATA_NEWS","").lower() in {"1","true","yes"},
    )
