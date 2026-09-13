"""Legendary-trader-inspired setup agents.

These agents translate well-known, publicly described trading principles into
objective research filters. They are approximations for screening/backtesting,
not claims that the original traders used these exact formulas.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TraderAgent:
    slug: str
    name: str
    setup: str


AGENTS = [
    TraderAgent("minervini", "Mark Minervini", "Trend Template / VCP"),
    TraderAgent("oneil", "William O'Neil", "CAN SLIM-style Breakout"),
    TraderAgent("weinstein", "Stan Weinstein", "Stage 2 Breakout"),
    TraderAgent("darvas", "Nicolas Darvas", "Darvas Box Breakout"),
    TraderAgent("livermore", "Jesse Livermore", "Pivot / Line of Least Resistance"),
    TraderAgent("qullamaggie", "Kristjan Kullamägi", "Momentum Breakout / EP-style"),
    TraderAgent("druckenmiller", "Stanley Druckenmiller", "Liquidity + Catalyst + Technical Confirmation"),
    TraderAgent("lawwaisum", "Law Wai-Sum", "Growth Momentum + Position/Swing Hybrid"),
    TraderAgent("martinluk", "Martin Luk", "Asymmetric 5:1 Momentum Swing"),
    TraderAgent("top500swing", "Top-500 Swing Agent", "2–7 Day Liquid Swing Ideas"),
    TraderAgent("highmomentumbeta", "High Momentum / High Beta Agent", "Fast-Mover Momentum + Beta/Volatility"),
    TraderAgent("superstock", "Superstock Agent", "Explosive Leader / Early Supertrend Candidate"),
    TraderAgent("brownmoose", "Brownmoose Agent", "B1/B2 Confluence + Retest + Multi-Target Plan"),
    TraderAgent("venu", "Venu Agent", "Position Leader + Rotation Swing"),
]


def _num(df: pd.DataFrame, name: str, default=0.0) -> pd.Series:
    if name not in df:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[name], errors="coerce").fillna(default)


def _bool(df: pd.DataFrame, name: str, default=False) -> pd.Series:
    if name not in df:
        return pd.Series(default, index=df.index, dtype=bool)
    s = df[name]
    if s.dtype == bool:
        return s.fillna(default)
    return s.fillna(default).astype(bool)


def _text(df: pd.DataFrame, name: str, default="") -> pd.Series:
    if name not in df:
        return pd.Series(default, index=df.index, dtype=object)
    return df[name].fillna(default).astype(str)


def _base(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_price"] = _num(out, "price")
    out["_atr"] = _num(out, "atr_pct")
    out["_adr"] = _num(out, "adr20_pct")
    out["_rs"] = _num(out, "rs20_vs_spy")
    out["_tech"] = _num(out, "technical_score")
    out["_form"] = _num(out, "formation_score")
    out["_rr"] = _num(out, "effective_rr")
    out["_runway"] = _num(out, "runway_to_next_resistance_pct")
    out["_rvol"] = _num(out, "intraday_rvol")
    out["_cat"] = _num(out, "catalyst_score")
    out["_score"] = _num(out, "market_hunt_score")
    out["_risk"] = _num(out, "risk_pct", 99)
    out["_stage"] = _text(out, "stage")
    out["_pattern"] = _text(out, "pattern").str.lower()
    out["_entry"] = _text(out, "entry_model")
    out["_theme"] = _text(out, "theme_state")
    out["_regime"] = _text(out, "market_regime_state")
    out["_rsi"] = _num(out, "rsi14", 50)
    out["_adv"] = _num(out, "avg_dollar_volume")
    out["_beta"] = _num(out, "beta", np.nan)
    out["_max_up30"] = _num(out, "max_up_day_30d_pct", 0)
    out["_tenpct_days30"] = _num(out, "ten_pct_up_days_30d", 0)
    out["_explosive30"] = _bool(out, "explosive_move_30d", False)
    out["_top500_liquid"] = out["_adv"].rank(method="first", ascending=False).le(500)
    return out


def _quality_gate(d: pd.DataFrame) -> pd.Series:
    return (
        d["_price"].ge(5)
        & d["_atr"].between(2, 10)
        & d["_adr"].ge(2)
        & d["_rr"].ge(1.5)
        & d["_risk"].le(6)
        & d["_stage"].isin(["CONFIRMED", "ARMED", "FORMING", "DISCOVER"])
    )


def _finalize(d: pd.DataFrame, agent: TraderAgent, score: pd.Series, matched: pd.Series, why: pd.Series) -> pd.DataFrame:
    cols = [
        "ticker", "company_name", "price", "stage", "theme", "market_hunt_score",
        "technical_score", "formation_score", "rs20_vs_spy", "rsi14", "atr_pct", "adr20_pct",
        "entry_trigger", "entry_model", "stop", "effective_target", "effective_rr",
        "runway_to_next_resistance_pct", "catalyst_status", "catalyst_score",
        "max_up_day_30d_pct", "ten_pct_up_days_30d", "explosive_move_30d",
        "ema20", "ema50", "ema200", "daily_support", "daily_resistance",
        "weekly_support", "weekly_resistance", "monthly_support", "monthly_resistance",
        "target_5", "target_8", "target_10", "next_higher_resistance",
        "intraday_rvol", "pattern",
    ]
    keep = [c for c in cols if c in d.columns]
    out = d.loc[matched, keep].copy()
    out.insert(0, "trader", agent.name)
    out.insert(1, "legendary_setup", agent.setup)
    out["legendary_score"] = score.loc[matched].round(1).clip(0, 100)
    out["setup_match"] = np.where(out["legendary_score"].ge(75), "STRONG", "WATCH")
    out["setup_reason"] = why.loc[matched]
    if "rsi14" in out.columns:
        rsi = pd.to_numeric(out["rsi14"], errors="coerce").fillna(50)
        out["rsi_state"] = np.select(
            [rsi.between(55, 72), rsi.between(72, 80), rsi.lt(45)],
            ["MOMENTUM SWEET SPOT", "STRONG / EXTENDED WATCH", "WEAK MOMENTUM"],
            default="NEUTRAL / RESET",
        )
    return out.sort_values(["legendary_score", "market_hunt_score" if "market_hunt_score" in out else "legendary_score"], ascending=False)


def _minervini(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        25 + d["_tech"] * 0.35 + d["_form"] * 0.30
        + d["_rs"].clip(-10, 20) * 0.8
        + np.where(d["_pattern"].str.contains("tight|vcp|contraction"), 12, 0)
        + np.where(d["_stage"].isin(["ARMED", "FORMING"]), 8, 0)
        + np.where(d["_theme"].isin(["STRONG", "LEADING"]), 5, 0)
    )
    matched = _quality_gate(d) & d["_rs"].ge(0) & d["_form"].ge(45) & d["_rr"].ge(2)
    why = pd.Series("Strong relative strength + tight constructive structure + favorable R/R", index=d.index)
    return _finalize(d, a, score, matched, why)


def _oneil(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        20 + d["_tech"] * 0.35 + d["_form"] * 0.25 + d["_rs"].clip(-10, 20) * 0.8
        + d["_cat"].clip(0, 100) * 0.12
        + np.where(d["_pattern"].str.contains("cup|handle|base|breakout|tight"), 10, 0)
        + np.where(d["_stage"].isin(["CONFIRMED", "ARMED"]), 8, 0)
    )
    matched = _quality_gate(d) & d["_rs"].ge(0) & d["_cat"].ge(20) & d["_rr"].ge(2)
    why = pd.Series("Leadership + base/breakout quality + catalyst/fundamental proxy + relative strength", index=d.index)
    return _finalize(d, a, score, matched, why)


def _weinstein(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        25 + d["_tech"] * 0.40 + d["_rs"].clip(-10, 20) * 0.9
        + np.where(d["_stage"].isin(["CONFIRMED", "ARMED"]), 12, 0)
        + np.where(d["_theme"].isin(["STRONG", "LEADING"]), 8, 0)
        + np.where(d["_regime"].isin(["STRONG", "BULLISH", "HEALTHY"]), 5, 0)
    )
    matched = _quality_gate(d) & d["_rs"].ge(0) & d["_stage"].isin(["CONFIRMED", "ARMED", "FORMING"]) & d["_runway"].ge(4)
    why = pd.Series("Stage-2-style trend strength + relative strength + sector confirmation + breakout runway", index=d.index)
    return _finalize(d, a, score, matched, why)


def _darvas(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        20 + d["_tech"] * 0.30 + d["_form"] * 0.35 + d["_rs"].clip(-10, 20) * 0.7
        + np.where(d["_pattern"].str.contains("box|range|tight|breakout|resistance"), 14, 0)
        + np.where(d["_stage"].isin(["ARMED", "CONFIRMED"]), 10, 0)
    )
    matched = _quality_gate(d) & d["_form"].ge(40) & d["_runway"].ge(4) & d["_rr"].ge(2)
    why = pd.Series("Tight price box/range near resistance with breakout potential and defined downside", index=d.index)
    return _finalize(d, a, score, matched, why)


def _livermore(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        20 + d["_tech"] * 0.38 + d["_rs"].clip(-10, 20) * 0.8
        + np.where(d["_stage"].isin(["ARMED", "CONFIRMED"]), 12, 0)
        + np.where(d["_entry"].eq("BREAKOUT"), 8, 0)
        + d["_rvol"].clip(0, 4) * 3
    )
    matched = _quality_gate(d) & d["_rs"].ge(0) & d["_stage"].isin(["ARMED", "CONFIRMED", "FORMING"]) & d["_runway"].ge(5)
    why = pd.Series("Pivotal resistance / line-of-least-resistance setup with momentum and clear invalidation", index=d.index)
    return _finalize(d, a, score, matched, why)


def _qullamaggie(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        15 + d["_tech"] * 0.28 + d["_form"] * 0.20 + d["_rs"].clip(-10, 20) * 0.7
        + d["_cat"].clip(0, 100) * 0.18
        + d["_rvol"].clip(0, 5) * 4
        + np.where(d["_atr"].ge(3), 8, 0)
        + np.where(d["_stage"].isin(["CONFIRMED", "ARMED"]), 8, 0)
    )
    matched = (
        _quality_gate(d) & d["_atr"].ge(3) & d["_adr"].ge(3)
        & d["_cat"].ge(25) & d["_rs"].ge(0) & d["_rr"].ge(2)
    )
    why = pd.Series("High-momentum/high-ADR stock with fresh catalyst, relative strength and breakout/retest asymmetry", index=d.index)
    return _finalize(d, a, score, matched, why)



def _druckenmiller(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        18 + d["_tech"] * 0.28 + d["_rs"].clip(-10, 20) * 0.9
        + d["_cat"].clip(0, 100) * 0.22
        + np.where(d["_theme"].isin(["STRONG", "LEADING"]), 10, 0)
        + np.where(d["_regime"].isin(["STRONG", "BULLISH", "HEALTHY"]), 7, 0)
        + np.where(d["_stage"].isin(["ARMED", "CONFIRMED"]), 8, 0)
    )
    matched = (
        _quality_gate(d)
        & d["_rs"].ge(0)
        & d["_cat"].ge(30)
        & d["_runway"].ge(5)
        & d["_rr"].ge(2.2)
        & d["_stage"].isin(["FORMING", "ARMED", "CONFIRMED"])
    )
    why = pd.Series(
        "Liquid leadership + meaningful catalyst + supportive market/theme context + technical price confirmation",
        index=d.index,
    )
    return _finalize(d, a, score, matched, why)


def _lawwaisum(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        16 + d["_tech"] * 0.30 + d["_form"] * 0.24
        + d["_rs"].clip(-10, 20) * 0.85
        + d["_cat"].clip(0, 100) * 0.15
        + np.where(d["_theme"].isin(["STRONG", "LEADING"]), 8, 0)
        + np.where(d["_stage"].isin(["FORMING", "ARMED", "CONFIRMED"]), 6, 0)
    )
    matched = (
        _quality_gate(d)
        & d["_rs"].ge(0)
        & d["_form"].ge(40)
        & d["_rr"].ge(2.2)
        & d["_runway"].ge(4)
    )
    why = pd.Series(
        "Growth-momentum leadership with constructive base/position structure, disciplined risk and room for a sustained move",
        index=d.index,
    )
    return _finalize(d, a, score, matched, why)


def _martinluk(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    score = (
        12 + d["_tech"] * 0.26 + d["_form"] * 0.20
        + d["_rs"].clip(-10, 20) * 0.75
        + d["_cat"].clip(0, 100) * 0.14
        + d["_rvol"].clip(0, 5) * 3.5
        + np.where(d["_rr"].ge(5), 18, np.where(d["_rr"].ge(3.5), 10, 0))
        + np.where(d["_stage"].isin(["ARMED", "CONFIRMED"]), 7, 0)
    )
    matched = (
        _quality_gate(d)
        & d["_rs"].ge(0)
        & d["_adr"].ge(2.5)
        & d["_rr"].ge(3.0)
        & d["_risk"].le(4.5)
        & d["_runway"].ge(5)
    )
    why = pd.Series(
        "High-momentum swing with tightly capped downside and strongly asymmetric upside; prioritizes 3:1+ and rewards 5:1 opportunities",
        index=d.index,
    )
    return _finalize(d, a, score, matched, why)


def _top500swing(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    """Rank swing ideas from the 500 most liquid stocks.

    This is intentionally broader than the final Market Hunt BUY gate.  Its job
    is idea discovery: surface the best 2–7 day structures first, then let the
    main live/catalyst/RR gates decide whether any becomes actionable.
    """
    liquidity_ok = d["_top500_liquid"] & d["_price"].ge(5)
    tradability_ok = d["_atr"].between(1.5, 10) & d["_adr"].ge(1.5)
    structure_ok = d["_tech"].ge(45) & d["_form"].ge(25)
    risk_ok = d["_risk"].le(7)
    stage_ok = ~d["_stage"].isin(["EXTENDED"])

    score = (
        10
        + d["_tech"] * 0.34
        + d["_form"] * 0.22
        + d["_rs"].clip(-15, 30) * 0.55
        + d["_cat"].clip(0, 100) * 0.08
        + d["_adr"].clip(0, 8) * 1.8
        + np.where(d["_rsi"].between(55, 72), 8,
          np.where(d["_rsi"].between(48, 55), 4,
          np.where(d["_rsi"].between(72, 78), 3,
          np.where(d["_rsi"].lt(45), -6, 0))))
        + np.where(d["_rr"].ge(2.5), 10, np.where(d["_rr"].ge(1.5), 6, np.where(d["_rr"].ge(1.0), 2, -4)))
        + np.where(d["_runway"].ge(8), 8, np.where(d["_runway"].ge(4), 5, np.where(d["_runway"].ge(2), 2, -4)))
        + np.where(d["_stage"].eq("CONFIRMED"), 10, 0)
        + np.where(d["_stage"].eq("ARMED"), 8, 0)
        + np.where(d["_stage"].eq("FORMING"), 6, 0)
        + np.where(d["_pattern"].str.contains("tight|pullback|flag|base|breakout|support|contraction"), 6, 0)
        - np.where(d["_risk"].gt(6), 5, 0)
    )

    matched = liquidity_ok & tradability_ok & structure_ok & risk_ok & stage_ok
    why = pd.Series(
        "Top-500 liquid swing candidate ranked on trend/structure, relative strength, volatility, R/R, resistance runway, catalyst and risk",
        index=d.index,
    )
    return _finalize(d, a, score, matched, why)


def _highmomentumbeta(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    """Discover and rank high-momentum, high-volatility/high-beta candidates.

    Discovery is deliberately broader than the final Market Hunt trade gate.
    Reported beta is rewarded when available; otherwise ATR/ADR are used as a
    fast-mover proxy.  R/R and resistance runway affect ranking rather than
    suppressing the entire discovery list.
    """
    beta_available = d["_beta"].notna()
    true_beta_ok = beta_available & d["_beta"].ge(1.3)
    fast_mover_ok = d["_atr"].ge(3.5) & d["_adr"].ge(3.5)
    liquidity_ok = d["_price"].ge(5) & d["_adv"].ge(20_000_000)
    momentum_ok = d["_rs"].ge(3) | d["_rvol"].ge(1.2)
    risk_ok = d["_risk"].le(7)

    score = (
        8
        + d["_rs"].clip(-10, 40) * 1.10
        + d["_atr"].clip(0, 10) * 2.2
        + d["_adr"].clip(0, 10) * 2.2
        + d["_rvol"].clip(0, 5) * 4.0
        + d["_tech"] * 0.18
        + d["_form"] * 0.10
        + d["_cat"].clip(0, 100) * 0.12
        + np.where(d["_rsi"].between(55, 75), 10,
          np.where(d["_rsi"].between(48, 55), 5,
          np.where(d["_rsi"].between(75, 82), 2,
          np.where(d["_rsi"].lt(45), -8, 0))))
        + np.where(true_beta_ok, 10, 0)
        + np.where(d["_rr"].ge(2.5), 8, np.where(d["_rr"].ge(1.5), 5, np.where(d["_rr"].ge(1.0), 2, -3)))
        + np.where(d["_runway"].ge(8), 7, np.where(d["_runway"].ge(4), 4, np.where(d["_runway"].ge(2), 1, -3)))
        + np.where(d["_stage"].isin(["CONFIRMED", "ARMED", "FORMING"]), 6, 0)
        + np.where(d["_cat"].ge(30), 4, 0)
    )

    matched = liquidity_ok & fast_mover_ok & momentum_ok & risk_ok
    why = pd.Series(
        "Fast-mover discovery: strong relative strength/RVOL plus high ATR/ADR; reported beta is rewarded when available, with R/R and runway used for ranking",
        index=d.index,
    )
    out = _finalize(d, a, score, matched, why)
    if out.empty:
        return out

    # Momentum-specific quality label.  This is a research setup grade, not the
    # final Market Hunt trade recommendation.
    strong_buy = (
        out["legendary_score"].ge(80)
        & pd.to_numeric(out.get("effective_rr"), errors="coerce").fillna(0).ge(2.0)
        & pd.to_numeric(out.get("runway_to_next_resistance_pct"), errors="coerce").fillna(0).ge(5.0)
        & pd.to_numeric(out.get("technical_score"), errors="coerce").fillna(0).ge(60)
        & pd.to_numeric(out.get("rs20_vs_spy"), errors="coerce").fillna(0).ge(8)
        & pd.to_numeric(out.get("rsi14"), errors="coerce").fillna(50).between(52, 78)
    )
    weak = (
        out["legendary_score"].lt(60)
        | pd.to_numeric(out.get("effective_rr"), errors="coerce").fillna(0).lt(1.2)
        | pd.to_numeric(out.get("runway_to_next_resistance_pct"), errors="coerce").fillna(0).lt(2.0)
    )
    out["momentum_grade"] = np.select(
        [strong_buy, weak],
        ["STRONG BUY SETUP", "WEAK SETUP"],
        default="GOOD SETUP",
    )
    return out


def _superstock(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    """Rank explosive liquid leaders that still have room to become outsized winners.

    This is a research discovery engine. It deliberately rewards evidence that a
    stock can make exceptional moves, while penalizing poor structure, weak
    relative strength, excessive risk and limited resistance runway.
    """
    liquidity_ok = d["_price"].ge(5) & d["_adv"].ge(20_000_000)
    volatility_ok = d["_atr"].between(2.5, 10) & d["_adr"].ge(3.0)
    explosive_ok = d["_explosive30"] | d["_max_up30"].ge(10)
    leadership_ok = d["_rs"].ge(5)
    structure_ok = d["_stage"].isin(["DISCOVER", "FORMING", "ARMED", "CONFIRMED"])
    risk_ok = d["_risk"].le(6.5)

    score = (
        5
        + d["_rs"].clip(-10, 40) * 1.25
        + d["_tech"] * 0.22
        + d["_form"] * 0.16
        + d["_cat"].clip(0, 100) * 0.18
        + d["_atr"].clip(0, 10) * 1.8
        + d["_adr"].clip(0, 10) * 2.0
        + d["_max_up30"].clip(0, 25) * 0.9
        + d["_tenpct_days30"].clip(0, 5) * 5.0
        + np.where(d["_explosive30"], 10, 0)
        + np.where(d["_rsi"].between(55, 75), 8,
          np.where(d["_rsi"].between(48, 55), 4,
          np.where(d["_rsi"].between(75, 82), 2,
          np.where(d["_rsi"].lt(45), -8, 0))))
        + np.where(d["_theme"].isin(["STRONG", "LEADING"]), 8, 0)
        + np.where(d["_rr"].ge(3.0), 10, np.where(d["_rr"].ge(2.0), 6, np.where(d["_rr"].ge(1.5), 2, -5)))
        + np.where(d["_runway"].ge(10), 10, np.where(d["_runway"].ge(6), 7, np.where(d["_runway"].ge(4), 3, -5)))
        + np.where(d["_stage"].eq("ARMED"), 7, 0)
        + np.where(d["_stage"].eq("FORMING"), 6, 0)
        + np.where(d["_stage"].eq("CONFIRMED"), 5, 0)
        + np.where(d["_pattern"].str.contains("tight|pullback|flag|base|breakout|contraction|support"), 7, 0)
        - np.where(d["_risk"].gt(5), 6, 0)
    )

    matched = liquidity_ok & volatility_ok & explosive_ok & leadership_ok & structure_ok & risk_ok
    why = pd.Series(
        "Explosive 30-day behavior + liquid leadership + high relative strength + constructive structure + catalyst/theme support + asymmetric runway",
        index=d.index,
    )
    out = _finalize(d, a, score, matched, why)
    if out.empty:
        return out

    sc = pd.to_numeric(out["legendary_score"], errors="coerce").fillna(0)
    rr = pd.to_numeric(out.get("effective_rr"), errors="coerce").fillna(0)
    runway = pd.to_numeric(out.get("runway_to_next_resistance_pct"), errors="coerce").fillna(0)
    rs = pd.to_numeric(out.get("rs20_vs_spy"), errors="coerce").fillna(0)
    tech = pd.to_numeric(out.get("technical_score"), errors="coerce").fillna(0)
    cat = pd.to_numeric(out.get("catalyst_score"), errors="coerce").fillna(0)
    maxup = pd.to_numeric(out.get("max_up_day_30d_pct"), errors="coerce").fillna(0)

    aplus = sc.ge(85) & rr.ge(2.5) & runway.ge(6) & rs.ge(10) & tech.ge(60) & maxup.ge(10)
    agrade = sc.ge(75) & rr.ge(2.0) & runway.ge(4) & rs.ge(7)
    bgrade = sc.ge(60)

    out["superstock_grade"] = np.select(
        [aplus, agrade, bgrade],
        ["A+ SUPERSTOCK", "A SUPERSTOCK", "B WATCH"],
        default="REJECT / LOW CONVICTION",
    )
    out["superstock_action"] = np.select(
        [aplus & cat.ge(30), aplus, agrade],
        ["PRIORITY RESEARCH", "WAIT FOR FRESH CATALYST", "WATCH FOR EARLY ENTRY"],
        default="MONITOR ONLY",
    )
    return out


def _brownmoose(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    """Approximate the observed Brownmoose subscriber-post framework.

    The model looks for liquid stocks where a staged B1/B2 plan can be built
    around confluence between moving averages, horizontal support and prior
    breakout/retest structure. Targets are mapped from overhead resistance.
    This is a research approximation, not a claim of the trader's exact rules.
    """
    price = d["_price"]
    ema20 = _num(d, "ema20", np.nan)
    ema50 = _num(d, "ema50", np.nan)
    ema200 = _num(d, "ema200", np.nan)
    ds = _num(d, "daily_support", np.nan)
    ws = _num(d, "weekly_support", np.nan)
    ms = _num(d, "monthly_support", np.nan)

    support_frame = pd.DataFrame({
        "EMA20": ema20,
        "EMA50": ema50,
        "EMA200": ema200,
        "DAILY_SUPPORT": ds,
        "WEEKLY_SUPPORT": ws,
        "MONTHLY_SUPPORT": ms,
    }, index=d.index)

    valid_support = support_frame.where(
        support_frame.gt(0) & support_frame.le(price * 1.015, axis=0)
    )

    b1 = valid_support.max(axis=1, skipna=True)
    b2_frame = valid_support.where(valid_support.lt(b1 * 0.992, axis=0))
    b2 = b2_frame.max(axis=1, skipna=True)

    b1_source = valid_support.eq(b1, axis=0).idxmax(axis=1)
    b2_source = b2_frame.eq(b2, axis=0).idxmax(axis=1)
    b1_source = b1_source.where(b1.notna(), "")
    b2_source = b2_source.where(b2.notna(), "")

    dr = _num(d, "daily_resistance", np.nan)
    wr = _num(d, "weekly_resistance", np.nan)
    mr = _num(d, "monthly_resistance", np.nan)
    t5 = _num(d, "target_5", np.nan)
    t8 = _num(d, "target_8", np.nan)
    t10 = _num(d, "target_10", np.nan)
    nh = _num(d, "next_higher_resistance", np.nan)

    resistance_frame = pd.DataFrame({
        "DAILY_RESISTANCE": dr,
        "WEEKLY_RESISTANCE": wr,
        "MONTHLY_RESISTANCE": mr,
        "NEXT_RESISTANCE": nh,
        "TARGET_5": t5,
        "TARGET_8": t8,
        "TARGET_10": t10,
    }, index=d.index).where(lambda x: x.gt(price, axis=0))

    def _ordered_targets(row):
        vals = sorted({round(float(v), 6) for v in row.dropna().tolist() if float(v) > 0})
        vals = vals[:3]
        return pd.Series(
            vals + [np.nan] * (3 - len(vals)),
            index=["brown_t1", "brown_t2", "brown_t3"],
        )

    targets = resistance_frame.apply(_ordered_targets, axis=1)

    # Confluence = number of meaningful technical references clustered near B1.
    dist = support_frame.sub(b1, axis=0).abs().div(b1.replace(0, np.nan), axis=0) * 100
    confluence_count = dist.le(2.0).sum(axis=1)
    b1_distance = ((price / b1) - 1) * 100
    b2_distance = ((price / b2) - 1) * 100

    # Preserve the scanner's existing technical stop when useful; otherwise use
    # a modest structural buffer under B2/B1 for research R/R calculations.
    existing_stop = _num(d, "stop", np.nan)
    structural_anchor = b2.fillna(b1)
    structural_stop = structural_anchor * 0.985
    invalidation = pd.concat([existing_stop, structural_stop], axis=1).min(axis=1, skipna=True)

    t1 = targets["brown_t1"]
    risk_per_share = (b1 - invalidation).clip(lower=0.01)
    reward_t1 = (t1 - b1).clip(lower=0)
    brown_rr_t1 = reward_t1 / risk_per_share

    near_b1 = b1.notna() & b1_distance.between(-1.0, 4.0)
    near_b2 = b2.notna() & b2_distance.between(-1.0, 3.0)
    breakout_retest = d["_pattern"].str.contains("breakout|retest|support|base|handle|pullback")
    ema200_context = b1_source.eq("EMA200") | b2_source.eq("EMA200")
    structure_ok = d["_stage"].isin(["DISCOVER", "FORMING", "ARMED", "CONFIRMED"])
    liquidity_ok = d["_price"].ge(5) & d["_adv"].ge(20_000_000)
    risk_ok = d["_risk"].le(7.0)

    score = (
        10
        + d["_tech"] * 0.28
        + d["_form"] * 0.22
        + d["_rs"].clip(-10, 25) * 0.55
        + confluence_count.clip(0, 6) * 7
        + np.where(near_b1, 12, 0)
        + np.where(near_b2, 8, 0)
        + np.where(ema200_context, 8, 0)
        + np.where(breakout_retest, 8, 0)
        + np.where(brown_rr_t1.ge(3), 10, np.where(brown_rr_t1.ge(2), 6, np.where(brown_rr_t1.ge(1.5), 3, -4)))
        + np.where(d["_stage"].eq("ARMED"), 7, 0)
        + np.where(d["_stage"].eq("CONFIRMED"), 5, 0)
        + np.where(d["_stage"].eq("FORMING"), 5, 0)
        + np.where(d["_rsi"].between(45, 68), 5, 0)
        - np.where(d["_risk"].gt(6), 5, 0)
    )

    matched = (
        liquidity_ok
        & structure_ok
        & risk_ok
        & b1.notna()
        & t1.notna()
        & confluence_count.ge(2)
        & brown_rr_t1.ge(1.3)
    )

    why = pd.Series(
        "Staged B1/B2 plan from clustered EMA/support references, breakout/retest structure and mapped T1/T2/T3 resistance targets",
        index=d.index,
    )
    out = _finalize(d, a, score, matched, why)
    if out.empty:
        return out

    idx = out.index
    out["brownmoose_confluence_score"] = (confluence_count.loc[idx].clip(0, 6) / 6 * 100).round(0)
    out["brown_b1"] = b1.loc[idx].round(2)
    out["brown_b1_source"] = b1_source.loc[idx]
    out["brown_b2"] = b2.loc[idx].round(2)
    out["brown_b2_source"] = b2_source.loc[idx]
    out["brown_invalidation"] = invalidation.loc[idx].round(2)
    out["brown_t1"] = targets.loc[idx, "brown_t1"].round(2)
    out["brown_t2"] = targets.loc[idx, "brown_t2"].round(2)
    out["brown_t3"] = targets.loc[idx, "brown_t3"].round(2)
    out["brown_rr_to_t1"] = brown_rr_t1.loc[idx].round(2)
    out["brown_b1_distance_pct"] = b1_distance.loc[idx].round(2)
    out["brown_b2_distance_pct"] = b2_distance.loc[idx].round(2)

    state = np.select(
        [
            near_b2.loc[idx],
            near_b1.loc[idx] & d.loc[idx, "_stage"].eq("CONFIRMED"),
            near_b1.loc[idx],
            d.loc[idx, "_stage"].eq("CONFIRMED"),
        ],
        ["B2 READY", "B1 READY / CONFIRMED", "B1 READY", "BREAKOUT CONFIRMED"],
        default="WATCH",
    )
    out["brownmoose_state"] = state
    out["brownmoose_grade"] = np.select(
        [
            out["legendary_score"].ge(85) & out["brownmoose_confluence_score"].ge(50) & out["brown_rr_to_t1"].ge(2.0),
            out["legendary_score"].ge(75) & out["brown_rr_to_t1"].ge(1.5),
            out["legendary_score"].ge(60),
        ],
        ["A+ PLAN", "A PLAN", "B WATCH"],
        default="LOW CONVICTION",
    )
    return out


def _venu(d: pd.DataFrame, a: TraderAgent) -> pd.DataFrame:
    """Approximate a research-driven leader/rotation framework.

    Two modes are surfaced:
    - POSITION LEADER: stronger trend, RS, catalyst/theme and structure for
      potentially longer holds.
    - ROTATION SWING: tactical pullback/breakout setups within strong themes.
    """
    ema20 = _num(d, "ema20", np.nan)
    ema50 = _num(d, "ema50", np.nan)
    ema200 = _num(d, "ema200", np.nan)
    price = d["_price"]

    trend20 = ema20.notna() & price.ge(ema20)
    trend50 = ema50.notna() & price.ge(ema50)
    trend200 = ema200.notna() & price.ge(ema200)
    stacked = ema20.notna() & ema50.notna() & ema200.notna() & ema20.ge(ema50) & ema50.ge(ema200)

    strong_theme = d["_theme"].isin(["STRONG", "LEADING"])
    leadership = d["_rs"].ge(5)
    strong_leadership = d["_rs"].ge(10)
    constructive_stage = d["_stage"].isin(["FORMING", "ARMED", "CONFIRMED"])
    pullback_pattern = d["_pattern"].str.contains("pullback|retest|support|tight|base|handle|contraction")
    breakout_pattern = d["_pattern"].str.contains("breakout|pivot|resistance|gap")
    liquid = d["_price"].ge(5) & d["_adv"].ge(20_000_000)
    risk_ok = d["_risk"].le(6.5)

    # Catalyst score is used as the current fundamental/event-strength proxy.
    # This avoids pretending we have exact earnings acceleration fields when
    # they are not present in the candidate frame.
    fundamental_proxy = d["_cat"].clip(0, 100)

    position_score = (
        8
        + d["_tech"] * 0.24
        + d["_form"] * 0.18
        + d["_rs"].clip(-10, 30) * 1.00
        + fundamental_proxy * 0.22
        + np.where(strong_theme, 10, 0)
        + np.where(stacked, 10, 0)
        + np.where(trend20 & trend50 & trend200, 8, 0)
        + np.where(strong_leadership, 8, 0)
        + np.where(d["_rr"].ge(2.5), 8, np.where(d["_rr"].ge(1.8), 4, -4))
        + np.where(d["_runway"].ge(8), 8, np.where(d["_runway"].ge(5), 5, -3))
        + np.where(d["_rsi"].between(52, 72), 5, 0)
    )

    rotation_score = (
        8
        + d["_tech"] * 0.26
        + d["_form"] * 0.22
        + d["_rs"].clip(-10, 25) * 0.75
        + fundamental_proxy * 0.14
        + np.where(strong_theme, 8, 0)
        + np.where(pullback_pattern, 10, 0)
        + np.where(breakout_pattern, 7, 0)
        + np.where(trend20 | trend50, 6, 0)
        + np.where(d["_stage"].eq("ARMED"), 8, 0)
        + np.where(d["_stage"].eq("CONFIRMED"), 6, 0)
        + np.where(d["_rr"].ge(2.0), 7, np.where(d["_rr"].ge(1.5), 3, -4))
        + np.where(d["_runway"].ge(5), 6, np.where(d["_runway"].ge(3), 3, -3))
    )

    position_match = (
        liquid & risk_ok & constructive_stage
        & leadership
        & strong_theme
        & (trend50 | trend200)
        & d["_cat"].ge(25)
        & d["_rr"].ge(1.8)
    )
    rotation_match = (
        liquid & risk_ok & constructive_stage
        & d["_rs"].ge(2)
        & (strong_theme | d["_cat"].ge(20))
        & (pullback_pattern | breakout_pattern)
        & d["_rr"].ge(1.4)
    )

    matched = position_match | rotation_match
    combined_score = pd.Series(
        np.where(position_match, np.maximum(position_score, rotation_score), rotation_score),
        index=d.index,
    )

    why = pd.Series(
        "Research-driven leader framework: theme leadership, catalyst/fundamental proxy, relative strength, trend alignment and constructive pullback/breakout timing",
        index=d.index,
    )
    out = _finalize(d, a, combined_score, matched, why)
    if out.empty:
        return out

    idx = out.index
    pm = position_match.loc[idx]
    rm = rotation_match.loc[idx]
    out["venu_mode"] = np.select(
        [pm & rm, pm, rm],
        ["POSITION LEADER + ROTATION", "POSITION LEADER", "ROTATION SWING"],
        default="WATCH",
    )
    out["venu_trend_stack"] = np.where(stacked.loc[idx], "EMA20>EMA50>EMA200", "NOT FULLY STACKED")
    out["venu_theme_leadership"] = np.where(strong_theme.loc[idx], "LEADING/STRONG", "MIXED")
    out["venu_fundamental_proxy_score"] = fundamental_proxy.loc[idx].round(0)
    out["venu_grade"] = np.select(
        [
            out["legendary_score"].ge(85)
            & pd.to_numeric(out.get("rs20_vs_spy"), errors="coerce").fillna(0).ge(10)
            & out["venu_fundamental_proxy_score"].ge(35),
            out["legendary_score"].ge(75),
            out["legendary_score"].ge(60),
        ],
        ["A+ LEADER", "A LEADER", "B WATCH"],
        default="LOW CONVICTION",
    )
    return out


_RUNNERS = {
    "minervini": _minervini,
    "oneil": _oneil,
    "weinstein": _weinstein,
    "darvas": _darvas,
    "livermore": _livermore,
    "qullamaggie": _qullamaggie,
    "druckenmiller": _druckenmiller,
    "lawwaisum": _lawwaisum,
    "martinluk": _martinluk,
    "top500swing": _top500swing,
    "highmomentumbeta": _highmomentumbeta,
    "superstock": _superstock,
    "brownmoose": _brownmoose,
    "venu": _venu,
}


def run_legendary_agents(candidates: pd.DataFrame, output_dir: Path, top_n: int = 25) -> pd.DataFrame:
    """Run all trader agents and publish one list per trader plus a consensus list."""
    if candidates is None or candidates.empty:
        return pd.DataFrame()

    d = _base(candidates)
    all_rows = []
    for agent in AGENTS:
        result = _RUNNERS[agent.slug](d, agent).head(top_n)
        result.to_csv(output_dir / f"trader_{agent.slug}.csv", index=False)
        if not result.empty:
            all_rows.append(result)

    if not all_rows:
        empty = pd.DataFrame()
        empty.to_csv(output_dir / "legendary_setups.csv", index=False)
        empty.to_csv(output_dir / "legendary_consensus.csv", index=False)
        return empty

    combined = pd.concat(all_rows, ignore_index=True)
    combined = combined.sort_values(["legendary_score", "market_hunt_score"], ascending=False)
    combined.to_csv(output_dir / "legendary_setups.csv", index=False)

    consensus = (
        combined.groupby("ticker", as_index=False)
        .agg(
            legendary_agent_count=("trader", "nunique"),
            legendary_avg_score=("legendary_score", "mean"),
            legendary_best_score=("legendary_score", "max"),
            legendary_traders=("trader", lambda s: " | ".join(sorted(set(s)))),
            legendary_setups=("legendary_setup", lambda s: " | ".join(sorted(set(s)))),
        )
        .sort_values(["legendary_agent_count", "legendary_best_score"], ascending=False)
    )
    consensus["legendary_avg_score"] = consensus["legendary_avg_score"].round(1)
    consensus.to_csv(output_dir / "legendary_consensus.csv", index=False)
    return combined
