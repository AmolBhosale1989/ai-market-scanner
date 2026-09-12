import argparse
import math
import numpy as np
import pandas as pd

from .config import (
    OUTPUT_DIR, MIN_PRICE, MIN_AVG_DOLLAR_VOLUME, TOP_N, BATCH_SIZE, BENCHMARK,
    CATALYST_ENRICH_LIMIT, CATALYST_STRONG_SCORE, CATALYST_ACTIVE_SCORE,
    MIN_DATA_COVERAGE, MIN_ANALYZABLE_COVERAGE, LIVE_ENRICH_LIMIT,
    THEME_PROFILE_LIMIT, PREFILTER_PERIOD,
)
from .catalysts import enrich_candidates
from .data import download_history, download_batch
from .indicators import add_indicators
from .live import enrich_live_candidates
from .prefilter import build_tradable_rows
from .stocks import analyze_dataframe
from .themes import rank_themes, enrich_candidate_themes
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
        return "BUY / CONFIRMED"
    if row["stage"]=="ARMED" and technical=="WAIT FOR TRIGGER":
        if score>=CATALYST_ACTIVE_SCORE:
            return "WAIT FOR TRIGGER + CATALYST"
        return "WAIT FOR TRIGGER"
    if row["stage"] in {"FORMING","DISCOVER"} and score>=CATALYST_STRONG_SCORE:
        return "WATCHLIST + CATALYST"
    return technical

def _prefilter_universe(universe: pd.DataFrame):
    tickers=universe["ticker"].dropna().astype(str).tolist()
    expected=len(tickers)
    rows=[]
    fetched=set()
    total_batches=math.ceil(expected/BATCH_SIZE)

    print(
        f"Fast tradability prefilter: {expected:,} master symbols "
        f"using {PREFILTER_PERIOD} daily data..."
    )
    for bi,start in enumerate(range(0,expected,BATCH_SIZE),1):
        batch=tickers[start:start+BATCH_SIZE]
        print(f"Prefilter {bi}/{total_batches}: {batch[0]} ... {batch[-1]}")
        histories=download_batch(batch,period=PREFILTER_PERIOD,interval="1d")
        fetched.update(histories.keys())
        pf=build_tradable_rows(histories)
        if not pf.empty:
            rows.append(pf)

    pfdf=pd.concat(rows,ignore_index=True) if rows else pd.DataFrame()
    coverage=len(fetched)/expected if expected else 0.0

    if pfdf.empty:
        return pfdf,coverage,len(fetched)

    names=universe[["ticker","name","exchange"]].copy()
    pfdf=pfdf.merge(names,on="ticker",how="left")
    pfdf=pfdf.sort_values(
        ["tradable","avg_dollar_volume20"],ascending=[False,False]
    ).reset_index(drop=True)
    pfdf.to_csv(OUTPUT_DIR/"tradable_universe.csv",index=False)
    return pfdf,coverage,len(fetched)

def run(refresh_universe: bool=False, limit: int|None=None, top_n: int=TOP_N):
    universe=load_or_build_universe(force_refresh=refresh_universe).sort_values("ticker").reset_index(drop=True)
    full_count=len(universe)
    if limit:
        universe=_representative_sample(universe,limit)
        print(f"Representative master sample: {len(universe):,} of {full_count:,} symbols")

    master_expected=len(universe)
    if master_expected==0:
        raise RuntimeError("Universe is empty.")

    print("Ranking market themes...")
    theme_table=rank_themes()
    if not theme_table.empty:
        print("\nTOP TRENDING THEMES")
        print(theme_table.head(10)[["theme_rank","theme","etf","theme_score","theme_state","rel5_vs_spy","rel20_vs_spy"]].to_string(index=False))

    # PASS 1: cheap liquidity/price gate across the broad master universe.
    pfdf,prefilter_coverage,prefilter_fetched=_prefilter_universe(universe)
    if prefilter_coverage < MIN_DATA_COVERAGE:
        _write_health(
            status="FAIL",
            master_universe_symbols=master_expected,
            prefilter_fetched_symbols=prefilter_fetched,
            prefilter_data_coverage=round(prefilter_coverage,4),
            tradable_symbols=0,
        )
        raise RuntimeError(
            "SCAN ABORTED: insufficient prefilter market-data coverage. "
            f"Fetched={prefilter_coverage:.1%} (min {MIN_DATA_COVERAGE:.0%})."
        )

    if pfdf.empty:
        raise RuntimeError("SCAN ABORTED: tradability prefilter returned no usable rows.")

    tradable_df=pfdf[pfdf["tradable"]].copy()
    tradable_tickers=tradable_df["ticker"].astype(str).tolist()
    tradable_count=len(tradable_tickers)
    print(
        f"TRADABLE UNIVERSE: {tradable_count:,}/{master_expected:,} "
        f"({tradable_count/master_expected:.1%}) passed price/liquidity gate"
    )
    if tradable_count==0:
        raise RuntimeError("SCAN ABORTED: no symbols passed the tradability gate.")

    company_names=dict(zip(universe["ticker"].astype(str),universe["name"].fillna("").astype(str)))

    # PASS 2: expensive one-year technical analysis only for tradable stocks.
    bench20=_benchmark_return20()
    rows=[]
    fetched=set()
    analyzable=set()
    total_batches=math.ceil(tradable_count/BATCH_SIZE)

    for bi,start in enumerate(range(0,tradable_count,BATCH_SIZE),1):
        batch=tradable_tickers[start:start+BATCH_SIZE]
        print(f"Deep scan {bi}/{total_batches}: {batch[0]} ... {batch[-1]}")
        histories=download_batch(batch,period="1y",interval="1d")
        fetched.update(histories.keys())

        for ticker,hist in histories.items():
            try:
                if hist is not None and len(hist)>=220:
                    analyzable.add(ticker)
                result=analyze_dataframe(ticker,hist,benchmark_return20=bench20)
                if not result:
                    continue
                # Defensive re-check in case liquidity changed between passes.
                if result["price"]<MIN_PRICE:
                    continue
                if result["avg_dollar_volume"]<MIN_AVG_DOLLAR_VOLUME:
                    continue
                result["company_name"]=company_names.get(ticker,"")
                rows.append(result)
            except Exception as e:
                print(f"{ticker}: {e}")

    deep_data_coverage=len(fetched)/tradable_count
    analyzable_coverage=len(analyzable)/tradable_count
    print(
        f"DATA HEALTH: prefilter {prefilter_fetched:,}/{master_expected:,} "
        f"({prefilter_coverage:.1%}); deep fetched {len(fetched):,}/{tradable_count:,} "
        f"({deep_data_coverage:.1%}); analyzable {len(analyzable):,}/{tradable_count:,} "
        f"({analyzable_coverage:.1%}); technical rows {len(rows):,}"
    )

    health={
        "status":"PASS",
        "master_universe_symbols":master_expected,
        "prefilter_fetched_symbols":prefilter_fetched,
        "prefilter_data_coverage":round(prefilter_coverage,4),
        "tradable_symbols":tradable_count,
        "tradable_pct_of_master":round(tradable_count/master_expected,4),
        "deep_fetched_symbols":len(fetched),
        "deep_data_coverage":round(deep_data_coverage,4),
        "analyzable_symbols":len(analyzable),
        "analyzable_coverage":round(analyzable_coverage,4),
        "technical_candidate_rows":len(rows),
        "min_prefilter_coverage_required":MIN_DATA_COVERAGE,
        "min_deep_analyzable_coverage_required":MIN_ANALYZABLE_COVERAGE,
    }

    if deep_data_coverage<MIN_DATA_COVERAGE or analyzable_coverage<MIN_ANALYZABLE_COVERAGE:
        health["status"]="FAIL"
        _write_health(**health)
        raise RuntimeError(
            "SCAN ABORTED: insufficient deep-scan coverage. "
            f"Fetched={deep_data_coverage:.1%} (min {MIN_DATA_COVERAGE:.0%}), "
            f"analyzable={analyzable_coverage:.1%} (min {MIN_ANALYZABLE_COVERAGE:.0%})."
        )

    if not rows:
        health["status"]="FAIL"
        _write_health(**health)
        raise RuntimeError("SCAN ABORTED: no valid candidate rows after a healthy deep scan.")

    df=pd.DataFrame(rows)
    stage_rank={"CONFIRMED":5,"ARMED":4,"FORMING":3,"DISCOVER":2,"EXTENDED":1,"REJECT":0}
    df["stage_rank"]=df["stage"].map(stage_rank).fillna(0)

    tech=pd.to_numeric(df["technical_score"],errors="coerce").fillna(0)
    form=pd.to_numeric(df["formation_score"],errors="coerce").fillna(0)
    rs=pd.to_numeric(df["rs20_vs_spy"],errors="coerce").fillna(0).clip(-20,20)
    risk=pd.to_numeric(df["risk_score"],errors="coerce").fillna(100)
    df["rank_score"]=(tech*0.65+form*0.20+rs*0.50-risk*0.15).round(1)

    print(f"Tagging up to {THEME_PROFILE_LIMIT} top candidates with leading themes...")
    df=enrich_candidate_themes(df,theme_table,limit=THEME_PROFILE_LIMIT)

    print(f"Enriching up to {CATALYST_ENRICH_LIMIT} top technical candidates with catalyst/news data...")
    df=enrich_candidates(df,limit=CATALYST_ENRICH_LIMIT)

    catalyst=pd.to_numeric(df["catalyst_score"],errors="coerce").fillna(0).clip(0,100)
    neg=df["negative_catalyst_risk"].fillna(False).astype(bool).astype(int)
    theme_bonus=pd.to_numeric(df["theme_bonus"],errors="coerce").fillna(0)
    df["final_score"]=(df["rank_score"].fillna(-100)+theme_bonus+catalyst*0.20-neg*15).round(1)
    df["final_decision"]=df.apply(_final_decision,axis=1)

    print(f"Checking live VWAP/opening-range/volume confirmation for up to {LIVE_ENRICH_LIMIT} advanced candidates...")
    df=enrich_live_candidates(df,limit=LIVE_ENRICH_LIMIT)

    live_score=pd.to_numeric(df["live_confirmation_score"],errors="coerce").fillna(0)
    live_bonus=np.where(df["live_status"].eq("LIVE"),live_score*0.10,0)
    df["market_hunt_score"]=(df["final_score"].fillna(-100)+live_bonus).round(1)

    all_out=OUTPUT_DIR/"all_candidates.csv"
    df.sort_values(["market_hunt_score","avg_dollar_volume"],ascending=[False,False]).to_csv(all_out,index=False)

    shortlist=df[df["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    shortlist=shortlist.sort_values(
        ["stage_rank","market_hunt_score","effective_rr"],ascending=[False,False,False]
    ).head(top_n)
    shortlist=shortlist.drop(columns=["stage_rank"],errors="ignore")

    out=OUTPUT_DIR/"latest_scan.csv"
    shortlist.to_csv(out,index=False)
    _write_health(**health)

    print("\nTOP MARKET HUNT CANDIDATES")
    cols=[
        "ticker","price","stage","theme","theme_score","market_hunt_score",
        "catalyst_score","entry_trigger","entry_model","stop","stop_basis","risk_pct","effective_target","effective_rr",
        "runway_to_next_resistance_pct","live_status","intraday_rvol",
        "live_confirmation_score","live_trade_action",
    ]
    print(shortlist[cols].to_string(index=False))
    print(f"\nSaved tradable universe: {OUTPUT_DIR/'tradable_universe.csv'}")
    print(f"Saved shortlist: {out}")
    print(f"Saved all technical candidates: {all_out}")
    print(f"Saved themes: {OUTPUT_DIR/'trending_themes.csv'}")
    print(f"Saved scan health: {OUTPUT_DIR/'scan_health.csv'}")
    return shortlist

if __name__=="__main__":
    p=argparse.ArgumentParser(description="Market Hunt V3 broad U.S. scanner")
    p.add_argument("--refresh-universe",action="store_true")
    p.add_argument("--limit",type=int,default=None)
    p.add_argument("--top",type=int,default=TOP_N)
    args=p.parse_args()
    run(refresh_universe=args.refresh_universe,limit=args.limit,top_n=args.top)
