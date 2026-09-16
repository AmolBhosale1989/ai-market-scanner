from __future__ import annotations

import math
import pandas as pd
import numpy as np


def bar_order_flow_proxy(session: pd.DataFrame) -> dict:
    """Bar-derived order-flow proxy.

    This is NOT true trade-at-bid/ask delta or Level 2 depth. It estimates
    directional pressure from 5-minute OHLCV, close location, volume impulse
    and VWAP-relative behavior.
    """
    if session is None or session.empty:
        return {
            "order_flow_mode": "BAR_PROXY",
            "order_flow_score": 0.0,
            "order_flow_state": "NO DATA",
            "buy_pressure_pct": math.nan,
            "sell_pressure_pct": math.nan,
            "signed_volume_proxy": math.nan,
            "volume_imbalance_proxy": math.nan,
            "close_location_value": math.nan,
            "volume_impulse": math.nan,
            "vwap_pressure": math.nan,
        }

    d = session.copy()
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["High", "Low", "Close", "Volume"])
    if d.empty:
        return {
            "order_flow_mode": "BAR_PROXY",
            "order_flow_score": 0.0,
            "order_flow_state": "NO DATA",
            "buy_pressure_pct": math.nan,
            "sell_pressure_pct": math.nan,
            "signed_volume_proxy": math.nan,
            "volume_imbalance_proxy": math.nan,
            "close_location_value": math.nan,
            "volume_impulse": math.nan,
            "vwap_pressure": math.nan,
        }

    rng = (d["High"] - d["Low"]).replace(0, np.nan)
    clv = (((d["Close"] - d["Low"]) - (d["High"] - d["Close"])) / rng).clip(-1, 1).fillna(0)
    volume = d["Volume"].fillna(0)
    signed = volume * clv
    total = float(volume.sum())
    signed_total = float(signed.sum())
    imbalance = signed_total / total if total > 0 else math.nan

    # Approximate volume participation: bars closing in upper/lower half.
    buy_vol = float(volume[clv > 0].sum())
    sell_vol = float(volume[clv < 0].sum())
    classified = buy_vol + sell_vol
    buy_pct = buy_vol / classified * 100 if classified > 0 else 50.0
    sell_pct = sell_vol / classified * 100 if classified > 0 else 50.0

    recent = d.tail(3)
    recent_vol = float(recent["Volume"].mean()) if not recent.empty else math.nan
    prior = d.iloc[:-3].tail(9)
    prior_vol = float(prior["Volume"].mean()) if not prior.empty else math.nan
    volume_impulse = recent_vol / prior_vol if math.isfinite(prior_vol) and prior_vol > 0 else math.nan

    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    denom = float(volume.sum())
    vwap = float((typical * volume).sum() / denom) if denom > 0 else math.nan
    last = float(d["Close"].iloc[-1])
    vwap_pressure = (last / vwap - 1) * 100 if math.isfinite(vwap) and vwap > 0 else math.nan

    clv_mean = float(clv.tail(6).mean()) if len(clv) else 0.0
    score = 50.0
    if math.isfinite(imbalance):
        score += max(-20, min(20, imbalance * 35))
    score += max(-12, min(12, clv_mean * 12))
    if math.isfinite(volume_impulse):
        score += max(-8, min(8, (volume_impulse - 1) * 8))
    if math.isfinite(vwap_pressure):
        score += max(-10, min(10, vwap_pressure * 2))
    score = max(0.0, min(100.0, score))

    if score >= 70:
        state = "STRONG BUYING PRESSURE"
    elif score >= 58:
        state = "BUYING PRESSURE"
    elif score <= 30:
        state = "STRONG SELLING PRESSURE"
    elif score <= 42:
        state = "SELLING PRESSURE"
    else:
        state = "BALANCED"

    return {
        "order_flow_mode": "BAR_PROXY",
        "order_flow_score": round(score, 1),
        "order_flow_state": state,
        "buy_pressure_pct": round(buy_pct, 1),
        "sell_pressure_pct": round(sell_pct, 1),
        "signed_volume_proxy": round(signed_total, 0),
        "volume_imbalance_proxy": round(imbalance * 100, 2) if math.isfinite(imbalance) else math.nan,
        "close_location_value": round(clv_mean, 3),
        "volume_impulse": round(volume_impulse, 2) if math.isfinite(volume_impulse) else math.nan,
        "vwap_pressure": round(vwap_pressure, 2) if math.isfinite(vwap_pressure) else math.nan,
    }
