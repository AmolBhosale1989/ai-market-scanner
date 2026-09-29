"""Optional enrichment policy; price, risk and snapshot gates remain mandatory."""
import os
import pandas as pd

TEXT_FIELDS = (
    "catalyst_status", "catalyst_type", "catalyst_bias", "catalyst_headline",
    "catalyst_provider", "catalyst_relevance", "news_freshness_status",
    "catalyst_materiality", "catalyst_gate_reason", "sec_status", "yahoo_status",
    "earnings_status", "catalyst_provider_states",
)
NUMERIC_FIELDS = ("catalyst_score", "catalyst_age_hours", "earnings_days", "rejected_news_count")
BOOL_FIELDS = ("catalyst_fresh", "negative_catalyst_risk", "catalyst_gate_ok")
OUTPUT_DATASETS = frozenset((
    "daily_prepared_candidates", "all_candidates", "latest_scan", "watchlist",
    "liquid_leaders", "v3_live_discovery", "v3_live_snapshot", "intraday_live",
    "recommended_trades", "legendary_setups", "legendary_consensus",
    "momentum_signals", "order_flow_strategy",
))


def disabled():
    return os.getenv("CATALYST_MODE", "optional").lower() == "disabled"


def truth(value):
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "1.0", "yes"}


def normalize(frame):
    """Label absent evidence without inventing freshness, events or numeric scores."""
    out = frame.copy()
    available = (out["catalyst_gate_ok"].map(truth) if "catalyst_gate_ok" in out
                 else pd.Series(False, index=out.index))
    if disabled():
        available[:] = False
    missing = ~available
    out["catalyst_availability"] = available.map({True: "available", False: "unavailable"})
    for name in TEXT_FIELDS:
        values = out[name] if name in out else pd.Series("unavailable", index=out.index)
        out[name] = values.fillna("unavailable").replace("", "unavailable").astype(object)
        out.loc[missing, name] = "unavailable"
    for name in NUMERIC_FIELDS:
        values = out[name] if name in out else pd.Series(float("nan"), index=out.index)
        out[name] = pd.to_numeric(values, errors="coerce").astype(float)
        out.loc[missing, name] = float("nan")
    for name in BOOL_FIELDS:
        out[name] = out[name].map(truth) if name in out else False
        out.loc[missing, name] = False
    return out
