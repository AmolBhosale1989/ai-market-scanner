from __future__ import annotations

import argparse
import math
import pandas as pd

from .config import (
    OUTPUT_DIR, MIN_HISTORY_DAYS, BACKTEST_PERIOD, BACKTEST_HORIZON_DAYS,
    BACKTEST_MAX_TICKERS, BACKTEST_SIGNAL_STRIDE, MIN_RUNWAY_PCT,
)
from .data import download_history
from .stocks import analyze_dataframe

RR_THRESHOLDS = [1.5, 2.0, 2.5, 3.0]

def _benchmark_ret20(spy, cutoff):
    if spy is None or spy.empty:
        return 0.0
    x=spy.loc[:cutoff]
    if len(x)<21:
        return 0.0
    return float((x["Close"].iloc[-1]/x["Close"].iloc[-21]-1)*100)

def _evaluate_trade(future, entry, stop, target, entry_condition="BREAKOUT"):
    entered=False
    entry_date=None
    for dt,row in future.iterrows():
        if not entered:
            high=float(row["High"])
            low=float(row["Low"])
            if entry_condition=="TOUCH_AND_RECLAIM":
                triggered=(low<=entry<=high)
            else:
                triggered=(high>=entry)
            if triggered:
                entered=True
                entry_date=dt
                if low<=stop:
                    return "STOP",entry_date,stop
                if high>=target:
                    return "TARGET",entry_date,target
            continue
        if float(row["Low"])<=stop:
            return "STOP",entry_date,stop
        if float(row["High"])>=target:
            return "TARGET",entry_date,target
    if not entered:
        return "NOT_TRIGGERED",None,math.nan
    return "TIME_EXIT",entry_date,float(future["Close"].iloc[-1])

def _threshold_summary(tdf,horizon,stride,tickers_tested):
    rows=[]
    for threshold in RR_THRESHOLDS:
        if tdf.empty:
            subset=tdf
        else:
            subset=tdf[
                (pd.to_numeric(tdf["effective_rr"],errors="coerce")>=threshold)
                & (pd.to_numeric(tdf["runway_pct"],errors="coerce")>=MIN_RUNWAY_PCT)
            ].copy()
        triggered=subset[subset["outcome"]!="NOT_TRIGGERED"].copy() if len(subset) else subset
        wins=triggered[triggered["r_multiple"]>0] if len(triggered) else triggered
        losses=triggered[triggered["r_multiple"]<=0] if len(triggered) else triggered
        rows.append({
            "rr_threshold":threshold,
            "signals":len(subset),
            "triggered":len(triggered),
            "trigger_rate_pct":round(len(triggered)/len(subset)*100,1) if len(subset) else 0,
            "wins":len(wins),
            "losses":len(losses),
            "win_rate_pct":round(len(wins)/len(triggered)*100,1) if len(triggered) else 0,
            "avg_return_pct":round(triggered["return_pct"].mean(),2) if len(triggered) else math.nan,
            "median_return_pct":round(triggered["return_pct"].median(),2) if len(triggered) else math.nan,
            "avg_r_multiple":round(triggered["r_multiple"].mean(),2) if len(triggered) else math.nan,
            "target_hit_rate_pct":round((triggered["outcome"]=="TARGET").mean()*100,1) if len(triggered) else 0,
            "stop_hit_rate_pct":round((triggered["outcome"]=="STOP").mean()*100,1) if len(triggered) else 0,
            "horizon_days":horizon,
            "signal_stride_days":stride,
            "tickers_tested":tickers_tested,
        })
    return pd.DataFrame(rows)

def run(tickers=None,max_tickers=BACKTEST_MAX_TICKERS,horizon=BACKTEST_HORIZON_DAYS,
        stride=BACKTEST_SIGNAL_STRIDE,period=BACKTEST_PERIOD):
    if tickers:
        symbols=[x.strip().upper() for x in tickers if x.strip()]
    else:
        source=OUTPUT_DIR/"tradable_universe.csv"
        if not source.exists():
            raise RuntimeError("Run Market Hunt first so outputs/tradable_universe.csv exists.")
        u=pd.read_csv(source)
        u=u[u["tradable"].astype(bool)] if "tradable" in u.columns else u
        if "avg_dollar_volume20" in u.columns:
            u=u.sort_values("avg_dollar_volume20",ascending=False)
        symbols=u["ticker"].astype(str).head(max_tickers).tolist()

    symbols=symbols[:max_tickers]
    spy=download_history("SPY",period,"1d")
    trades=[]

    for n,ticker in enumerate(symbols,1):
        print(f"Backtest {n}/{len(symbols)}: {ticker}")
        d=download_history(ticker,period,"1d")
        if d is None or len(d)<MIN_HISTORY_DAYS+horizon+5:
            continue

        last_signal_index=len(d)-horizon-1
        for i in range(MIN_HISTORY_DAYS,last_signal_index,stride):
            hist=d.iloc[:i+1]
            cutoff=hist.index[-1]
            result=analyze_dataframe(ticker,hist,benchmark_return20=_benchmark_ret20(spy,cutoff))
            if not result or result["technical_stage"] not in {"ARMED","CONFIRMED"}:
                continue

            runway=float(result["runway_to_next_resistance_pct"]) if pd.notna(result["runway_to_next_resistance_pct"]) else math.nan
            if not math.isfinite(runway) or runway<MIN_RUNWAY_PCT:
                continue

            future=d.iloc[i+1:i+1+horizon]
            if future.empty:
                continue

            entry=float(result["entry_trigger"])
            stop=float(result["stop"])
            target=float(result["effective_target"])
            effective_rr=float(result["effective_rr"])
            outcome,entry_date,exit_price=_evaluate_trade(
                future,entry,stop,target,result.get("entry_condition","BREAKOUT")
            )

            ret_pct=math.nan
            r_multiple=math.nan
            if outcome!="NOT_TRIGGERED" and math.isfinite(exit_price):
                ret_pct=(exit_price/entry-1)*100
                initial_risk=entry-stop
                if initial_risk>0:
                    r_multiple=(exit_price-entry)/initial_risk

            trades.append({
                "ticker":ticker,
                "signal_date":str(pd.Timestamp(cutoff).date()),
                "technical_stage":result["technical_stage"],
                "entry":round(entry,2),
                "entry_model":result.get("entry_model",""),
                "entry_condition":result.get("entry_condition","BREAKOUT"),
                "retest_reference":result.get("retest_reference",math.nan),
                "retest_distance_pct":result.get("retest_distance_pct",math.nan),
                "stop":round(stop,2),
                "stop_basis":result.get("stop_basis",""),
                "risk_pct":result.get("risk_pct",math.nan),
                "effective_target":round(target,2),
                "effective_target_pct":result["effective_target_pct"],
                "effective_rr":round(effective_rr,2),
                "runway_pct":runway,
                "outcome":outcome,
                "entry_date":str(pd.Timestamp(entry_date).date()) if entry_date is not None else "",
                "exit_price":round(exit_price,2) if math.isfinite(exit_price) else math.nan,
                "return_pct":round(ret_pct,2) if math.isfinite(ret_pct) else math.nan,
                "r_multiple":round(r_multiple,2) if math.isfinite(r_multiple) else math.nan,
            })

    tdf=pd.DataFrame(trades)
    tdf.to_csv(OUTPUT_DIR/"backtest_trades.csv",index=False)

    thresholds=_threshold_summary(tdf,horizon,stride,len(symbols))
    thresholds.to_csv(OUTPUT_DIR/"backtest_thresholds.csv",index=False)

    strict=thresholds[thresholds["rr_threshold"].eq(2.5)].copy()
    strict.to_csv(OUTPUT_DIR/"backtest_summary.csv",index=False)

    print("\nR/R THRESHOLD CALIBRATION")
    print(thresholds.to_string(index=False))
    return strict,tdf

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Market Hunt historical walk-forward calibration")
    p.add_argument("--tickers",default="")
    p.add_argument("--max-tickers",type=int,default=BACKTEST_MAX_TICKERS)
    p.add_argument("--horizon",type=int,default=BACKTEST_HORIZON_DAYS)
    p.add_argument("--stride",type=int,default=BACKTEST_SIGNAL_STRIDE)
    p.add_argument("--period",default=BACKTEST_PERIOD)
    args=p.parse_args()
    tickers=[x for x in args.tickers.split(",") if x.strip()] if args.tickers else None
    run(tickers=tickers,max_tickers=args.max_tickers,horizon=args.horizon,stride=args.stride,period=args.period)
