from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .control_plane import read_dataset, write_dataset


def _num(s):
    return pd.to_numeric(s,errors="coerce")


def build_performance_reports(journal: pd.DataFrame | None=None):
    if journal is None:
        journal=read_dataset("paper_journal",required=False)

    summary_cols=[
        "candidate_signals","pre_entry_invalidated","signals","open_signals","closed_signals",
        "target_hits","failed_breakouts","invalidated",
        "win_rate_pct","target_hit_rate_pct","hit_5pct_rate","hit_8pct_rate","hit_10pct_rate",
        "avg_return_pct","median_return_pct","avg_mfe_pct","avg_mae_pct",
        "avg_r_multiple","median_r_multiple","profit_factor_r","expectancy_r"
    ]
    if journal is None or journal.empty:
        out=pd.DataFrame([{c:0 if c.endswith("signals") or c in {"signals","open_signals","closed_signals","target_hits","failed_breakouts","invalidated"} else math.nan for c in summary_cols}])
        write_dataset("performance_summary",out,entity_key=None)
        write_dataset("performance_by_setup",pd.DataFrame(),entity_key=None)
        return out,pd.DataFrame()

    j=journal.copy()
    outcome=j.get("outcome",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str)
    triggered=j.get("triggered_at_et",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str).str.strip().ne("")
    confirmed=j.get("live_confirmed_at_et",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str).str.strip().ne("")
    entered=triggered | confirmed | outcome.isin(["TARGET_HIT","FAILED_BREAKOUT"])
    pre_entry_invalidated=(~entered) & outcome.eq("INVALIDATED")

    trades=j[entered].copy()
    trade_outcome=trades.get("outcome",pd.Series("",index=trades.index,dtype=object)).fillna("").astype(str)
    closed=trades[trade_outcome.str.len()>0].copy()
    r=_num(closed.get("r_multiple",pd.Series(index=closed.index,dtype=float)))
    ret=_num(closed.get("return_pct",pd.Series(index=closed.index,dtype=float)))
    wins=r[r>0]
    losses=r[r<=0]

    gross_win=float(wins.sum()) if len(wins) else 0.0
    gross_loss=abs(float(losses.sum())) if len(losses) else 0.0
    profit_factor=(gross_win/gross_loss) if gross_loss>0 else (math.inf if gross_win>0 else math.nan)

    row={
        "candidate_signals":len(j),
        "pre_entry_invalidated":int(pre_entry_invalidated.sum()),
        "signals":len(trades),
        "open_signals":int(trade_outcome.eq("").sum()),
        "closed_signals":len(closed),
        "target_hits":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("TARGET_HIT").sum()),
        "failed_breakouts":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("FAILED_BREAKOUT").sum()),
        "invalidated":int(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("INVALIDATED").sum()),
        "win_rate_pct":round(float((r>0).mean()*100),1) if r.notna().any() else math.nan,
        "target_hit_rate_pct":round(float(closed.get("outcome",pd.Series(index=closed.index,dtype=object)).eq("TARGET_HIT").mean()*100),1) if len(closed) else math.nan,
        "hit_5pct_rate":round(float(closed.get("hit_5pct",pd.Series(False,index=closed.index)).fillna(False).astype(bool).mean()*100),1) if len(closed) else math.nan,
        "hit_8pct_rate":round(float(closed.get("hit_8pct",pd.Series(False,index=closed.index)).fillna(False).astype(bool).mean()*100),1) if len(closed) else math.nan,
        "hit_10pct_rate":round(float(closed.get("hit_10pct",pd.Series(False,index=closed.index)).fillna(False).astype(bool).mean()*100),1) if len(closed) else math.nan,
        "avg_return_pct":round(float(ret.mean()),2) if ret.notna().any() else math.nan,
        "avg_mfe_pct":round(float(_num(closed.get("mfe_pct",pd.Series(index=closed.index,dtype=float))).mean()),2) if _num(closed.get("mfe_pct",pd.Series(index=closed.index,dtype=float))).notna().any() else math.nan,
        "avg_mae_pct":round(float(_num(closed.get("mae_pct",pd.Series(index=closed.index,dtype=float))).mean()),2) if _num(closed.get("mae_pct",pd.Series(index=closed.index,dtype=float))).notna().any() else math.nan,
        "median_return_pct":round(float(ret.median()),2) if ret.notna().any() else math.nan,
        "avg_r_multiple":round(float(r.mean()),2) if r.notna().any() else math.nan,
        "median_r_multiple":round(float(r.median()),2) if r.notna().any() else math.nan,
        "profit_factor_r":round(float(profit_factor),2) if math.isfinite(profit_factor) else profit_factor,
        "expectancy_r":round(float(r.mean()),2) if r.notna().any() else math.nan,
    }
    summary=pd.DataFrame([row])
    write_dataset("performance_summary",summary,entity_key=None)

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
                "hit_5pct_rate":round(float(g.get("hit_5pct",pd.Series(False,index=g.index)).fillna(False).astype(bool).mean()*100),1),
                "hit_8pct_rate":round(float(g.get("hit_8pct",pd.Series(False,index=g.index)).fillna(False).astype(bool).mean()*100),1),
                "hit_10pct_rate":round(float(g.get("hit_10pct",pd.Series(False,index=g.index)).fillna(False).astype(bool).mean()*100),1),
                "avg_return_pct":round(float(gret.mean()),2) if gret.notna().any() else math.nan,
                "avg_r_multiple":round(float(gr.mean()),2) if gr.notna().any() else math.nan,
            })
    grouped=pd.DataFrame(rows)
    if not grouped.empty:
        grouped=grouped.sort_values(["dimension","closed_signals"],ascending=[True,False])
    write_dataset("performance_by_setup",grouped,entity_key=None)
    return summary,grouped


def build_empirical_calibration(journal: pd.DataFrame | None=None, min_samples: int=20):
    if journal is None:
        journal=read_dataset("paper_journal",required=False)

    cols=["metric","bin","samples","wins","raw_win_rate_pct","smoothed_probability_pct","avg_r_multiple","calibration_status"]
    if journal is None or journal.empty:
        out=pd.DataFrame(columns=cols)
        write_dataset("probability_calibration",out,entity_key=None)
        return out

    j=journal.copy()
    outcome=j.get("outcome",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str)
    triggered=j.get("triggered_at_et",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str).str.strip().ne("")
    confirmed=j.get("live_confirmed_at_et",pd.Series("",index=j.index,dtype=object)).fillna("").astype(str).str.strip().ne("")
    entered=triggered | confirmed | outcome.isin(["TARGET_HIT","FAILED_BREAKOUT"])
    j=j[entered & outcome.str.len().gt(0)].copy()
    if j.empty:
        out=pd.DataFrame(columns=cols)
        write_dataset("probability_calibration",out,entity_key=None)
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
    write_dataset("probability_calibration",out,entity_key=None)
    return out


if __name__=="__main__":
    summary,grouped=build_performance_reports()
    calibration=build_empirical_calibration()
    if not summary.empty:
        print("\nFORWARD PERFORMANCE")
        print(summary.to_string(index=False))
    print(f"Grouped performance rows: {len(grouped)}")
    print(f"Calibration rows: {len(calibration)}")
