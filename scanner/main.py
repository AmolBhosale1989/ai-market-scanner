import argparse
import math
import numpy as np
import pandas as pd

from .config import (
    OUTPUT_DIR, MIN_PRICE, MIN_AVG_DOLLAR_VOLUME, TOP_N, BATCH_SIZE, BENCHMARK,
    CATALYST_ENRICH_LIMIT, CATALYST_STRONG_SCORE, CATALYST_ACTIVE_SCORE,
    MIN_DATA_COVERAGE, MIN_ANALYZABLE_COVERAGE,
)
from .catalysts import enrich_candidates
from .data import download_history, download_batch
from .indicators import add_indicators
from .stocks import analyze_dataframe
from .universe import load_or_build_universe

def _benchmark_return20():
    df=download_history(BENCHMARK,"3mo","1d")
    if len(df)<25:
        raise RuntimeError("Benchmark data unavailable or incomplete for SPY.")
    d=add_indicators(df)
    value=float(d.iloc[-1]["RET20"])
    if not math.isfinite(value):
        raise RuntimeError("Benchmark RET20 is invalid for SPY.")
    return value

def _representative_sample(universe: pd.DataFrame, limit: int) -> pd.DataFrame:
    if limit<=0 or limit>=len(universe):
        return universe.copy()
    idx=np.linspace(0,len(universe)-1,num=limit,dtype=int)
    return universe.iloc[idx].reset_index(drop=True)

def _write_health(**kwargs):
    pd.DataFrame([kwargs]).to_csv(OUTPUT_DIR/"scan_health.csv",index=False)

def _final_decision(row):
    technical=row["decision"]
    score=pd.to_numeric(pd.Series([row.get("catalyst_score",0)]),errors="coerce").fillna(0).iloc[0]
    negative=bool(row.get("negative_catalyst_risk",False))

    if negative and row["stage"] in {"CONFIRMED","ARMED"}:
        return "NO TRADE / NEGATIVE CATALYST"
    if row["stage"]=="CONFIRMED" and technical=="BUY / CONFIRMED":
        if score>=CATALYST_ACTIVE_SCORE:
            return "BUY / CONFIRMED + CATALYST"
        return "WAIT / NO FRESH CATALYST"
    if row["stage"]=="ARMED" and technical=="WAIT FOR TRIGGER":
        if score>=CATALYST_ACTIVE_SCORE:
            return "WAIT FOR TRIGGER + CATALYST"
        return "WAIT FOR TRIGGER"
    if row["stage"] in {"FORMING","DISCOVER"} and score>=CATALYST_STRONG_SCORE:
        return "WATCHLIST + CATALYST"
    return technical

def run(refresh_universe: bool=False, limit: int|None=None, top_n: int=TOP_N):
    universe=load_or_build_universe(force_refresh=refresh_universe).sort_values("ticker").reset_index(drop=True)
    full_count=len(universe)
    if limit:
        universe=_representative_sample(universe,limit)
        print(f"Representative sample: {len(universe):,} of {full_count:,} symbols")

    tickers=universe["ticker"].dropna().astype(str).tolist()
    company_names=dict(zip(universe["ticker"].astype(str),universe["name"].fillna("").astype(str)))
    expected=len(tickers)
    print(f"Universe used: {expected:,} symbols")
    if expected==0:
        raise RuntimeError("Universe is empty.")

    bench20=_benchmark_return20()
    rows=[]
    fetched=set()
    analyzable=set()
    total_batches=math.ceil(expected/BATCH_SIZE)

    for bi,start in enumerate(range(0,expected,BATCH_SIZE),1):
        batch=tickers[start:start+BATCH_SIZE]
        print(f"Batch {bi}/{total_batches}: {batch[0]} ... {batch[-1]}")
        histories=download_batch(batch,period="1y",interval="1d")
        fetched.update(histories.keys())

        for ticker,hist in histories.items():
            try:
                if hist is not None and len(hist)>=220:
                    analyzable.add(ticker)
                result=analyze_dataframe(ticker,hist,benchmark_return20=bench20)
                if not result:
                    continue
                if result["price"]<MIN_PRICE:
                    continue
                if result["avg_dollar_volume"]<MIN_AVG_DOLLAR_VOLUME:
                    continue
                result["company_name"]=company_names.get(ticker,"")
                rows.append(result)
            except Exception as e:
                print(f"{ticker}: {e}")

    data_coverage=len(fetched)/expected
    analyzable_coverage=len(analyzable)/expected
    print(
        f"DATA HEALTH: fetched {len(fetched):,}/{expected:,} "
        f"({data_coverage:.1%}); analyzable {len(analyzable):,}/{expected:,} "
        f"({analyzable_coverage:.1%}); liquid rows {len(rows):,}"
    )

    health={
        "status":"PASS",
        "universe_symbols":expected,
        "fetched_symbols":len(fetched),
        "data_coverage":round(data_coverage,4),
        "analyzable_symbols":len(analyzable),
        "analyzable_coverage":round(analyzable_coverage,4),
        "liquid_candidate_rows":len(rows),
        "min_data_coverage_required":MIN_DATA_COVERAGE,
        "min_analyzable_coverage_required":MIN_ANALYZABLE_COVERAGE,
    }

    if data_coverage<MIN_DATA_COVERAGE or analyzable_coverage<MIN_ANALYZABLE_COVERAGE:
        health["status"]="FAIL"
        _write_health(**health)
        raise RuntimeError(
            "SCAN ABORTED: insufficient market-data coverage. "
            f"Fetched={data_coverage:.1%} (min {MIN_DATA_COVERAGE:.0%}), "
            f"analyzable={analyzable_coverage:.1%} (min {MIN_ANALYZABLE_COVERAGE:.0%})."
        )

    if not rows:
        health["status"]="FAIL"
        _write_health(**health)
        raise RuntimeError("SCAN ABORTED: no valid liquid candidate rows after a healthy download.")

    df=pd.DataFrame(rows)
    stage_rank={"CONFIRMED":5,"ARMED":4,"FORMING":3,"DISCOVER":2,"EXTENDED":1,"REJECT":0}
    df["stage_rank"]=df["stage"].map(stage_rank).fillna(0)

    tech=pd.to_numeric(df["technical_score"],errors="coerce").fillna(0)
    form=pd.to_numeric(df["formation_score"],errors="coerce").fillna(0)
    rs=pd.to_numeric(df["rs20_vs_spy"],errors="coerce").fillna(0).clip(-20,20)
    risk=pd.to_numeric(df["risk_score"],errors="coerce").fillna(100)
    df["rank_score"]=(tech*0.65+form*0.20+rs*0.50-risk*0.15).round(1)

    print(f"Enriching up to {CATALYST_ENRICH_LIMIT} top technical candidates with catalyst/news data...")
    df=enrich_candidates(df,limit=CATALYST_ENRICH_LIMIT)

    catalyst=pd.to_numeric(df["catalyst_score"],errors="coerce").fillna(0).clip(0,100)
    neg=df["negative_catalyst_risk"].fillna(False).astype(bool).astype(int)
    df["final_score"]=(df["rank_score"].fillna(-100)+catalyst*0.20-neg*15).round(1)
    df["final_decision"]=df.apply(_final_decision,axis=1)

    all_out=OUTPUT_DIR/"all_candidates.csv"
    df.sort_values(["final_score","avg_dollar_volume"],ascending=[False,False]).to_csv(all_out,index=False)

    shortlist=df[df["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    shortlist=shortlist.sort_values(
        ["stage_rank","final_score","rr_to_8pct"],ascending=[False,False,False]
    ).head(top_n)
    shortlist=shortlist.drop(columns=["stage_rank"],errors="ignore")

    out=OUTPUT_DIR/"latest_scan.csv"
    shortlist.to_csv(out,index=False)
    _write_health(**health)

    print("\nTOP MARKET HUNT CANDIDATES")
    cols=[
        "ticker","company_name","price","stage","final_score","catalyst_score",
        "catalyst_status","catalyst_relevance","catalyst_type","earnings_days",
        "entry_trigger","stop","target_8","rr_to_8pct",
        "runway_to_next_resistance_pct","final_decision",
    ]
    print(shortlist[cols].to_string(index=False))
    print(f"\nSaved shortlist: {out}")
    print(f"Saved all liquid candidates: {all_out}")
    print(f"Saved scan health: {OUTPUT_DIR/'scan_health.csv'}")
    return shortlist

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Market Hunt V3 broad U.S. scanner")
    p.add_argument("--refresh-universe",action="store_true")
    p.add_argument("--limit",type=int,default=None)
    p.add_argument("--top",type=int,default=TOP_N)
    args=p.parse_args()
    run(refresh_universe=args.refresh_universe,limit=args.limit,top_n=args.top)
