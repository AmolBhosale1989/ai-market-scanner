from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Any

import pandas as pd


V7_SCHEMA = "7.0.0-paper"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _label(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text else "__MISSING__"


@dataclass(frozen=True)
class AllocationSettings:
    paper_equity: float = 100_000.0
    max_positions: int = 8
    risk_per_position_pct: float = 0.50
    max_total_risk_pct: float = 3.00
    max_position_notional_pct: float = 20.0
    max_sector_positions: int = 2
    max_theme_positions: int = 2
    min_effective_rr: float = 2.5
    min_p5_lower_probability: float = 25.0
    allowed_confidence: tuple[str, ...] = ("HIGH", "MODERATE")


def _hold(reason: str, settings: AllocationSettings, candidates: int = 0):
    return pd.DataFrame(), {
        "schema_version": V7_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "HOLD_SHADOW",
        "paper_only": True,
        "broker_execution_enabled": False,
        "reason": reason,
        "candidates_seen": candidates,
        "positions": 0,
        "settings": asdict(settings),
    }


def allocate_paper_portfolio(
    candidates: pd.DataFrame,
    v6_model_payload: dict[str, Any],
    settings: AllocationSettings | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    settings = settings or AllocationSettings()
    frame = candidates.copy() if candidates is not None else pd.DataFrame()
    if str(v6_model_payload.get("promotion_status", "")) != "VALIDATED_SHADOW":
        return _hold("V6 must be VALIDATED_SHADOW", settings, len(frame))
    if frame.empty:
        return _hold("no V6 candidates", settings)

    score_source = frame["v6_robust_score"] if "v6_robust_score" in frame else pd.Series(float("nan"), index=frame.index)
    score = pd.to_numeric(score_source, errors="coerce")
    frame = frame.assign(_score=score).sort_values("_score", ascending=False)
    selected: list[dict[str, Any]] = []
    sector_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}
    seen_tickers: set[str] = set()
    rejected: dict[str, int] = {}
    risk_budget = settings.paper_equity * settings.risk_per_position_pct / 100.0
    total_risk_limit = settings.paper_equity * settings.max_total_risk_pct / 100.0
    max_notional = settings.paper_equity * settings.max_position_notional_pct / 100.0
    total_risk = 0.0

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for _, row in frame.iterrows():
        if len(selected) >= settings.max_positions:
            reject("MAX_POSITIONS")
            continue
        ticker = _label(row.get("ticker"))
        if ticker == "__MISSING__" or ticker in seen_tickers:
            reject("DUPLICATE_OR_MISSING_TICKER")
            continue
        if str(row.get("v6_decision", "ABSTAIN")).upper() != "RANK":
            reject("V6_ABSTAIN")
            continue
        if str(row.get("v6_confidence", "LOW")).upper() not in settings.allowed_confidence:
            reject("LOW_CONFIDENCE")
            continue
        rr = _number(row.get("effective_rr"))
        lower_p5 = _number(row.get("v6_p5_lower"))
        if rr is None or rr < settings.min_effective_rr:
            reject("LOW_RR")
            continue
        if lower_p5 is None or lower_p5 < settings.min_p5_lower_probability:
            reject("LOW_CONSERVATIVE_PROBABILITY")
            continue
        entry = _number(row.get("entry_trigger"))
        stop = _number(row.get("stop"))
        if entry is None or stop is None or entry <= 0 or stop <= 0 or stop >= entry:
            reject("INVALID_RISK_LEVELS")
            continue
        sector = _label(row.get("sector", row.get("sector_name")))
        theme = _label(row.get("theme"))
        if sector_counts.get(sector, 0) >= settings.max_sector_positions:
            reject("SECTOR_CAP")
            continue
        if theme_counts.get(theme, 0) >= settings.max_theme_positions:
            reject("THEME_CAP")
            continue
        remaining_risk = total_risk_limit - total_risk
        allowed_risk = min(risk_budget, remaining_risk)
        if allowed_risk <= 0:
            reject("TOTAL_RISK_CAP")
            continue
        per_share_risk = entry - stop
        shares_by_risk = math.floor(allowed_risk / per_share_risk)
        shares_by_notional = math.floor(max_notional / entry)
        shares = min(shares_by_risk, shares_by_notional)
        if shares < 1:
            reject("POSITION_TOO_SMALL")
            continue
        position_risk = shares * per_share_risk
        notional = shares * entry
        total_risk += position_risk
        record = row.drop(labels=["_score"], errors="ignore").to_dict()
        record.update({
            "v7_allocation_rank": len(selected) + 1,
            "v7_paper_shares": shares,
            "v7_entry": round(entry, 4),
            "v7_stop": round(stop, 4),
            "v7_position_notional": round(notional, 2),
            "v7_position_risk": round(position_risk, 2),
            "v7_position_risk_pct": round(position_risk / settings.paper_equity * 100, 4),
            "v7_cumulative_risk_pct": round(total_risk / settings.paper_equity * 100, 4),
            "v7_paper_only": True,
            "v7_action": "PAPER_PLAN",
        })
        selected.append(record)
        seen_tickers.add(ticker)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        theme_counts[theme] = theme_counts.get(theme, 0) + 1

    allocations = pd.DataFrame(selected)
    health = {
        "schema_version": V7_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PAPER_PLAN_READY" if selected else "HOLD_NO_ELIGIBLE_POSITIONS",
        "paper_only": True,
        "broker_execution_enabled": False,
        "v6_model_version": str(v6_model_payload.get("model_version", "")),
        "candidates_seen": len(frame),
        "positions": len(selected),
        "planned_total_risk": round(total_risk, 2),
        "planned_total_risk_pct": round(total_risk / settings.paper_equity * 100, 4),
        "planned_notional": round(pd.to_numeric(allocations.get("v7_position_notional"), errors="coerce").sum(), 2) if len(allocations) else 0.0,
        "sector_counts": sector_counts,
        "theme_counts": theme_counts,
        "rejected": rejected,
        "settings": asdict(settings),
    }
    return allocations, health
