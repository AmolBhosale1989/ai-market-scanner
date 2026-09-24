import math


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def signal_card_detail(row):
    """Return truthful card detail for strict recommendations or momentum-only signals."""
    score = _finite(row.get("market_hunt_score"))
    reward_risk = _finite(row.get("effective_rr"))
    if score is not None and reward_risk is not None:
        decision = str(row.get("final_decision", row.get("decision", "RESEARCH")))
        return f"{decision} · score {score:.1f} · R/R {reward_risk:.2f}×"

    signal = str(row.get("momentum_signal", row.get("signal", ""))).strip()
    if signal:
        details = [signal]
        price = _finite(row.get("current_price", row.get("price")))
        relative = _finite(row.get("rel_vs_spy_live", row.get("rel_vs_spy_pct")))
        rvol = _finite(row.get("live_intraday_rvol", row.get("intraday_rvol")))
        if price is not None:
            details.append(f"price ${price:.2f}")
        if relative is not None:
            details.append(f"vs SPY {relative:+.2f}%")
        if rvol is not None:
            details.append(f"RVOL {rvol:.2f}×")
        return " · ".join(details)

    return str(row.get("final_decision", row.get("decision", "RESEARCH")))
