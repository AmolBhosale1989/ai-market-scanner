from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from .config import OUTPUT_DIR


def _num(s):
    return pd.to_numeric(s,errors="coerce")


def build_performance_reports(journal: pd.DataFrame | None=None):
    if journal is None:
        path=OUTPUT_DIR/"paper_journal.csv"
        journal=pd.read_csv(path) if path.exists() else pd.DataFrame()

    summary_cols=[
        "signals","open_signals","closed_signals","target_hits","failed_breakouts","invalidated",
        "win_rate_pct","target_hit_rate_pct","avg_return_pct","median_return_pct",
        "avg_r_multiple","median_r_multiple","profit_factor_r","expectancy_r"
    ]
    if journal is None or journal.empty:
        out=pd.DataFrame([{c:0 if c.endswith("signals") or c in {"signals","open_signals","closed_signals","target_hits","failed_breakouts","invalidated"} else math.nan for c in summary_cols}])
        out.to_csv(OUTPUT_DIR/"performance_summary.csv",index=False)
        pd.DataFrame().to_csv(OUTPUT_DIR/"performance_by_setup.csv",index=False)
        return out,pd.DataFrame()

    j=journal.copy()
    closed=j[j.get("outcome",pd.Series(index=j.index,dtype=object)).fillna("").astype(str).str.len()>0].copy()
    r=_num(closed.get("r_multiple",pd.Series(index=closed.index,dtype=float)))
    ret=_num(closed.get("return_pct",pd.Series(index=closed.index,dtype=float)))
    wins=r[r>0]
    losses=r[r<=0]

    gross_win=float(wins.sum()) if len(wins) else 0.0
    gross_loss=abs(float(losses.sum())) if len(losses) else 0.0
    profit_factor=(gross_win/gross_loss) if gross_loss>0 else (math.inf if gross_win>0 else math.nan)

    row={
        "signals":len(j),
        "open_signals":int(j.get("outcome",pd.Series(index=j.index,dtype=object)).fillna("").astype(str).eq("").sum()),
        "closed_signals":len(closed),
        "target_hits":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("TARGET_HIT").sum()),
        "failed_breakouts":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("FAILED_BREAKOUT").sum()),
        "invalidated":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("INVALIDATED").sum()),
        "win_rate_pct":round(float((r>0).mean()*100),1) if r.notna().any() else math.nan,
        "target_hit_rate_pct":round(float(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("TARGET_HIT").mean()*100),1) if len(closed) else math.nan,
        "avg_return_pct":round(float(ret.mean()),2) if ret.notna().any() else math.nan,
        "median_return_pct":round(float(ret.median()),2) if ret.notna().any() else math.nan,
        "avg_r_multiple":round(float(r.mean()),2) if r.notna().any() else math.nan,
        "median_r_multiple":round(float(r.median()),2) if r.notna().any() else math.nan,
        "profit_factor_r":round(float(profit_factor),2) if math.isfinite(profit_factor) else profit_factor,
        "expectancy_r":round(float(r.mean()),2) if r.notna().any() else math.nan,
    }
    summary=pd.DataFrame([row])
    summary.to_csv(OUTPUT_DIR/"performance_summary.csv",index=False)

    group_cols=[c for c in ["entry_model","market_regime_state","theme","catalyst_status","stage"] if c in closed.columns]
    rows=[]
    for col in group_cols:
        for value,g in closed.groupby(col,dropna=False):
            gr=_num(g.get("r_multiple",pd.Series(index=g.index,dtype=float)))
            gret=_num(g.get("return_pct",pd.Series(index=g.index,dtype=float)))
            rows.append({
                "dimension":col,
                "value":str(value),
                "closed_signals":len(g),
                "win_rate_pct":round(float((gr>0).mean()*100),1) if gr.notna().any() else math.nan,
                "target_hit_rate_pct":round(float(g.get("outcome",pd.Series(index=g.index,dtype=object)).eq("TARGET_HIT").mean()*100),1),
                "avg_return_pct":round(float(gret.mean()),2) if gret.notna().any() else math.nan,
                "avg_r_multiple":round(float(gr.mean()),2) if gr.notna().any() else math.nan,
            })
    grouped=pd.DataFrame(rows)
    if not grouped.empty:
        grouped=grouped.sort_values(["dimension","closed_signals"],ascending=[True,False])
    grouped.to_csv(OUTPUT_DIR/"performance_by_setup.csv",index=False)
    return summary,grouped


def build_empirical_calibration(journal: pd.DataFrame | None=None, min_samples: int=20):
    if journal is None:
        path=OUTPUT_DIR/"paper_journal.csv"
        journal=pd.read_csv(path) if path.exists() else pd.DataFrame()

    cols=["metric","bin","samples","wins","raw_win_rate_pct","smoothed_probability_pct","avg_r_multiple","calibration_status"]
    if journal is None or journal.empty:
        out=pd.DataFrame(columns=cols)
        out.to_csv(OUTPUT_DIR/"probability_calibration.csv",index=False)
        return out

    j=journal.copy()
    j=j[j.get("outcome",pd.Series(index=j.index,dtype=object)).fillna("").astype(str).str.len()>0].copy()
    if j.empty:
        out=pd.DataFrame(columns=cols)
        out.to_csv(OUTPUT_DIR/"probability_calibration.csv",index=False)
        return out

    r=_num(j.get("r_multiple",pd.Series(index=j.index,dtype=float)))
    j["_win"]=r>0
    j["_r"]=r
    rows=[]

    specs=[
        ("market_hunt_score",[0,45,55,65,75,1000]),
        ("technical_score",[0,40,55,70,85,1000]),
        ("effective_rr",[0,2,2.5,3,4,1000]),
        ("live_confirmation_score",[0,40,60,80,1000]),
    ]
    for metric,bins in specs:
        if metric not in j.columns:
            continue
        vals=_num(j[metric])
        if vals.notna().sum()==0:
            continue
        bucket=pd.cut(vals,bins=bins,right=False,include_lowest=True)
        for b,g in j.assign(_bin=bucket).dropna(subset=["_bin"]).groupby("_bin",observed=True):
            n=len(g); wins=int(g["_win"].sum())
            # Beta(2,2) smoothing prevents tiny samples from showing 0%/100%.
            smooth=(wins+2)/(n+4)*100
            rows.append({
                "metric":metric,
                "bin":str(b),
                "samples":n,
                "wins":wins,
                "raw_win_rate_pct":round(wins/n*100,1) if n else math.nan,
                "smoothed_probability_pct":round(smooth,1),
                "avg_r_multiple":round(float(g["_r"].mean()),2) if g["_r"].notna().any() else math.nan,
                "calibration_status":"USABLE" if n>=min_samples else "INSUFFICIENT_DATA",
            })

    out=pd.DataFrame(rows,columns=cols)
    out.to_csv(OUTPUT_DIR/"probability_calibration.csv",index=False)
    return out
