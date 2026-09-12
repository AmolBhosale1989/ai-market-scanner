from __future__ import annotations

import math
import time
import pandas as pd
import yfinance as yf

from .config import OUTPUT_DIR, THEME_PROFILE_LIMIT, THEME_BONUS_MAX
from .data import download_history
from .indicators import add_indicators

THEMES = {
    "Semiconductors": {"etf": "SMH", "keywords": ["semiconductor", "chip", "integrated circuit"]},
    "AI & Robotics": {"etf": "BOTZ", "keywords": ["artificial intelligence", "robotics", "automation", "machine learning"]},
    "Cloud Computing": {"etf": "SKYY", "keywords": ["cloud", "software infrastructure", "application software"]},
    "Cybersecurity": {"etf": "HACK", "keywords": ["cybersecurity", "security software", "network security"]},
    "Biotechnology": {"etf": "XBI", "keywords": ["biotechnology", "biotech"]},
    "Genomics": {"etf": "ARKG", "keywords": ["genomic", "genetics", "gene therapy", "gene editing"]},
    "Defense & Aerospace": {"etf": "ITA", "keywords": ["aerospace", "defense"]},
    "Energy": {"etf": "XLE", "keywords": ["oil", "gas", "energy", "exploration", "petroleum"]},
    "Oil Services": {"etf": "OIH", "keywords": ["oilfield", "drilling", "oil & gas equipment", "oil services"]},
    "Uranium & Nuclear": {"etf": "URA", "keywords": ["uranium", "nuclear"]},
    "Copper & Mining": {"etf": "COPX", "keywords": ["copper", "metal mining", "diversified metals"]},
    "Gold Miners": {"etf": "GDX", "keywords": ["gold", "precious metals"]},
    "Clean Energy": {"etf": "ICLN", "keywords": ["solar", "renewable", "clean energy", "wind"]},
    "Infrastructure": {"etf": "PAVE", "keywords": ["infrastructure", "engineering", "construction", "industrial machinery"]},
    "Homebuilders": {"etf": "XHB", "keywords": ["residential construction", "homebuilding", "building products"]},
    "Regional Banks": {"etf": "KRE", "keywords": ["regional bank", "banks regional"]},
    "Fintech": {"etf": "FINX", "keywords": ["financial technology", "fintech", "payment"]},
    "Cannabis": {"etf": "MSOS", "keywords": ["cannabis", "marijuana"]},
}

def _ret(d: pd.DataFrame, n: int):
    if len(d) <= n:
        return math.nan
    return float((d["Close"].iloc[-1] / d["Close"].iloc[-1-n] - 1) * 100)

def rank_themes():
    rows=[]
    spy=download_history("SPY","6mo","1d")
    spy_d=add_indicators(spy) if len(spy)>=70 else pd.DataFrame()
    spy5=_ret(spy_d,5) if not spy_d.empty else 0.0
    spy20=_ret(spy_d,20) if not spy_d.empty else 0.0
    spy60=_ret(spy_d,60) if not spy_d.empty else 0.0

    for theme,meta in THEMES.items():
        etf=meta["etf"]
        try:
            raw=download_history(etf,"6mo","1d")
            if len(raw)<70:
                continue
            d=add_indicators(raw)
            r5=_ret(d,5)
            r20=_ret(d,20)
            r60=_ret(d,60)
            last=d.iloc[-1]

            rel5=r5-spy5
            rel20=r20-spy20
            rel60=r60-spy60
            trend=0
            if last["Close"]>last["EMA20"]:
                trend+=8
            if last["EMA20"]>last["EMA50"]:
                trend+=8
            if last["EMA50"]>last["EMA200"]:
                trend+=8

            # Weight recent momentum most, while requiring some persistence.
            score=50 + rel5*3.0 + rel20*1.5 + rel60*0.35 + trend
            score=max(0,min(100,score))

            if score>=75 and rel20>0:
                state="LEADING"
            elif score>=60 and rel20>0:
                state="STRONG"
            elif score>=50:
                state="NEUTRAL"
            else:
                state="WEAK"

            rows.append({
                "theme":theme,
                "etf":etf,
                "theme_score":round(score,1),
                "theme_state":state,
                "ret5_pct":round(r5,2),
                "ret20_pct":round(r20,2),
                "ret60_pct":round(r60,2),
                "rel5_vs_spy":round(rel5,2),
                "rel20_vs_spy":round(rel20,2),
                "rel60_vs_spy":round(rel60,2),
            })
        except Exception as e:
            print(f"Theme {theme}/{etf}: {e}")

    out=pd.DataFrame(rows)
    if not out.empty:
        out=out.sort_values(["theme_score","rel20_vs_spy"],ascending=[False,False]).reset_index(drop=True)
        out["theme_rank"]=range(1,len(out)+1)
        out.to_csv(OUTPUT_DIR/"trending_themes.csv",index=False)
    return out

def _profile_text(info):
    return " ".join([
        str(info.get("sector") or ""),
        str(info.get("industry") or ""),
        str(info.get("longBusinessSummary") or ""),
    ]).lower()

def _match_theme(text: str, theme_table: pd.DataFrame):
    best=None
    for theme,meta in THEMES.items():
        if not any(k in text for k in meta["keywords"]):
            continue
        row=theme_table[theme_table["theme"].eq(theme)]
        if row.empty:
            continue
        r=row.iloc[0]
        candidate={
            "theme":theme,
            "score":float(r["theme_score"]),
            "state":str(r["theme_state"]),
            "etf":str(r["etf"]),
        }
        if best is None or candidate["score"]>best["score"]:
            best=candidate
    return best

def enrich_candidate_themes(df: pd.DataFrame, theme_table: pd.DataFrame, limit: int=THEME_PROFILE_LIMIT):
    out=df.copy()
    out["theme"]="UNCLASSIFIED"
    out["theme_etf"]=""
    out["theme_score"]=0.0
    out["theme_state"]="NONE"
    out["theme_bonus"]=0.0

    if out.empty or theme_table.empty:
        return out

    eligible=out[out["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    eligible=eligible.sort_values(["stage_rank","rank_score"],ascending=[False,False]).head(limit)

    for idx,row in eligible.iterrows():
        ticker=str(row["ticker"])
        try:
            obj=yf.Ticker(ticker)
            try:
                info=obj.get_info()
            except Exception:
                info=obj.info
            if not isinstance(info,dict):
                continue
            text=_profile_text(info)
            matched=_match_theme(text,theme_table)
            if not matched:
                continue

            score=matched["score"]
            # Theme can help ranking, but never become a mandatory trade gate.
            bonus=max(0.0,min(THEME_BONUS_MAX,(score-50)/50*THEME_BONUS_MAX))
            out.at[idx,"theme"]=matched["theme"]
            out.at[idx,"theme_etf"]=matched["etf"]
            out.at[idx,"theme_score"]=round(score,1)
            out.at[idx,"theme_state"]=matched["state"]
            out.at[idx,"theme_bonus"]=round(bonus,2)
        except Exception:
            pass
        time.sleep(0.03)

    return out
