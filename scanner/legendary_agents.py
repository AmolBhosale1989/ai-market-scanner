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
    out["_adv"] = _num(out, "avg_dollar_volume")
    out["_beta"] = _num(out, "beta", np.nan)
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
        "technical_score", "formation_score", "rs20_vs_spy", "atr_pct", "adr20_pct",
        "entry_trigger", "entry_model", "stop", "effective_target", "effective_rr",
        "runway_to_next_resistance_pct", "catalyst_status", "catalyst_score",
        "intraday_rvol", "pattern",
    ]
    keep = [c for c in cols if c in d.columns]
    out = d.loc[matched, keep].copy()
    out.insert(0, "trader", agent.name)
    out.insert(1, "legendary_setup", agent.setup)
    out["legendary_score"] = score.loc[matched].round(1).clip(0, 100)
    out["setup_match"] = np.where(out["legendary_score"].ge(75), "STRONG", "WATCH")
    out["setup_reason"] = why.loc[matched]
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
