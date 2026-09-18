from __future__ import annotations

import math
import time
import pandas as pd

from .config import OUTPUT_DIR, THEME_PROFILE_LIMIT, THEME_BONUS_MAX
from .warehouse import history as download_history
from .indicators import add_indicators

STATIC_THEME_MEMBERS = {
    "Cybersecurity": {"CRWD","PANW","FTNT","ZS","S","CYBR","OKTA","TENB","RBRK","VRNS","QLYS","GEN","CHKP"},
    "Semiconductors": {"NVDA","AMD","AVGO","MU","MRVL","ARM","INTC","QCOM","TSM","ASML","LRCX","AMAT","KLAC","MPWR","ON"},
    "Cloud Computing": {"SNOW","DDOG","NET","MDB","NOW","CRM","ORCL","AMZN","MSFT","GTLB","ESTC"},
    "Biotechnology": {"MRNA","BMRN","VRTX","REGN","ALNY","NBIX","IONS","CRSP","BEAM","NTLA","IOVA"},
    "Crypto & Digital Infrastructure": {"CIFR","IREN","MARA","RIOT","CLSK","HUT","WULF","CORZ","BTDR","BITF","CAN","ARBK","APLD"},
}

THEMES = {
    "Semiconductors": {"etf":"SMH","industries":["semiconductors","semiconductor equipment"],"keywords":["semiconductor","chip","integrated circuit"]},
    "AI & Robotics": {"etf":"BOTZ","industries":[],"keywords":["artificial intelligence","robotics","automation","machine learning"]},
    "Cloud Computing": {"etf":"SKYY","industries":[],"keywords":["cloud computing","cloud platform","saas"]},
    "Cybersecurity": {"etf":"HACK","industries":[],"keywords":["cybersecurity","security software","network security"]},
    "Biotechnology": {"etf":"XBI","industries":["biotechnology"],"keywords":["biotechnology","biotech"]},
    "Genomics": {"etf":"ARKG","industries":[],"keywords":["genomic","genetics","gene therapy","gene editing"]},
    "Defense & Aerospace": {"etf":"ITA","industries":["aerospace & defense"],"keywords":["aerospace","defense"]},
    "Energy": {"etf":"XLE","industries":["oil & gas e&p","oil & gas integrated","oil & gas midstream","oil & gas refining & marketing"],"keywords":["oil","gas","energy","exploration","petroleum"]},
    "Oil Services": {"etf":"OIH","industries":["oil & gas equipment & services","oil & gas drilling"],"keywords":["oilfield","drilling","oil services"]},
    "Uranium & Nuclear": {"etf":"URA","industries":["uranium"],"keywords":["uranium","nuclear"]},
    "Copper & Mining": {"etf":"COPX","industries":["copper","other industrial metals & mining"],"keywords":["copper","metal mining","diversified metals"]},
    "Gold Miners": {"etf":"GDX","industries":["gold"],"keywords":["gold mining","gold miner","precious metals"]},
    "Clean Energy": {"etf":"ICLN","industries":["solar","utilities - renewable"],"keywords":["solar","renewable","clean energy","wind"]},
    "Infrastructure": {"etf":"PAVE","industries":["engineering & construction","specialty industrial machinery"],"keywords":["infrastructure","engineering","construction"]},
    "Homebuilders": {"etf":"XHB","industries":["residential construction","building products & equipment"],"keywords":["homebuilding","homebuilder","residential construction"]},
    "Regional Banks": {"etf":"KRE","industries":["banks - regional"],"keywords":["regional bank"]},
    "Fintech": {"etf":"FINX","industries":[],"keywords":["financial technology","fintech","digital payments"]},
    "Crypto & Digital Infrastructure": {"etf":"WGMI","industries":[],"keywords":["bitcoin mining","crypto mining","cryptocurrency mining","digital infrastructure","data center"]},
    "Cannabis": {"etf":"MSOS","industries":[],"keywords":["cannabis","marijuana"]},
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
            r5=_ret(d,5); r20=_ret(d,20); r60=_ret(d,60)
            last=d.iloc[-1]
            rel5=r5-spy5; rel20=r20-spy20; rel60=r60-spy60
            trend=0
            if last["Close"]>last["EMA20"]: trend+=8
            if last["EMA20"]>last["EMA50"]: trend+=8
            if last["EMA50"]>last["EMA200"]: trend+=8

            score=max(0,min(100,50+rel5*3.0+rel20*1.5+rel60*0.35+trend))
            if score>=75 and rel20>0: state="LEADING"
            elif score>=60 and rel20>0: state="STRONG"
            elif score>=50: state="NEUTRAL"
            else: state="WEAK"

            rows.append({
                "theme":theme,"etf":etf,"theme_score":round(score,1),"theme_state":state,
                "ret5_pct":round(r5,2),"ret20_pct":round(r20,2),"ret60_pct":round(r60,2),
                "rel5_vs_spy":round(rel5,2),"rel20_vs_spy":round(rel20,2),"rel60_vs_spy":round(rel60,2),
            })
        except Exception as e:
            print(f"Theme {theme}/{etf}: {e}")

    out=pd.DataFrame(rows)
    if not out.empty:
        out=out.sort_values(["theme_score","rel20_vs_spy"],ascending=[False,False]).reset_index(drop=True)
        out["theme_rank"]=range(1,len(out)+1)
        out.to_csv(OUTPUT_DIR/"trending_themes.csv",index=False)
    return out

def _theme_row(theme, theme_table):
    row=theme_table[theme_table["theme"].eq(theme)]
    if row.empty:
        return None
    r=row.iloc[0]
    return {"theme":theme,"score":float(r["theme_score"]),"state":str(r["theme_state"]),"etf":str(r["etf"])}

def _match_theme(info: dict, theme_table: pd.DataFrame):
    sector=str(info.get("sector") or "").strip().lower()
    industry=str(info.get("industry") or "").strip().lower()
    summary=str(info.get("longBusinessSummary") or "").strip().lower()

    # High-confidence match: Yahoo industry directly maps to a theme.
    direct=[]
    for theme,meta in THEMES.items():
        for phrase in meta.get("industries",[]):
            if phrase and phrase in industry:
                row=_theme_row(theme,theme_table)
                if row:
                    direct.append({**row,"confidence":0.95,"source":"INDUSTRY","reason":f"{industry} -> {phrase}"})
                    break
    if direct:
        return max(direct,key=lambda x:x["score"])

    # Medium confidence: sector + a specific keyword in industry or summary.
    medium=[]
    combined=f"{industry} {summary}"
    for theme,meta in THEMES.items():
        matches=[k for k in meta.get("keywords",[]) if k in combined]
        if matches:
            row=_theme_row(theme,theme_table)
            if row:
                # Generic keywords in a long summary are not enough to drive a trade veto.
                confidence=0.75 if any(k in industry for k in matches) else 0.55
                source="INDUSTRY_KEYWORD" if confidence>=0.7 else "SUMMARY_KEYWORD"
                medium.append({**row,"confidence":confidence,"source":source,"reason":matches[0]})
    if medium:
        return max(medium,key=lambda x:(x["confidence"],x["score"]))
    return None

def enrich_candidate_themes(df: pd.DataFrame, theme_table: pd.DataFrame, limit: int=THEME_PROFILE_LIMIT):
    out=df.copy()
    out["theme"]="UNCLASSIFIED"; out["theme_etf"]=""; out["theme_score"]=0.0
    out["theme_state"]="NONE"; out["theme_bonus"]=0.0
    out["theme_match_confidence"]=0.0; out["theme_match_source"]="NONE"; out["theme_match_reason"]=""

    if out.empty or theme_table.empty:
        return out

    eligible=out[out["stage"].isin(["CONFIRMED","ARMED","FORMING","DISCOVER"])].copy()
    eligible=eligible.sort_values(["stage_rank","rank_score"],ascending=[False,False]).head(limit)

    for idx,row in eligible.iterrows():
        ticker=str(row["ticker"])
        try:
            matched=None
            for static_theme,members in STATIC_THEME_MEMBERS.items():
                if ticker in members:
                    row_match=_theme_row(static_theme,theme_table)
                    if row_match:
                        matched={**row_match,"confidence":1.0,"source":"STATIC_CONSTITUENT","reason":"verified theme membership"}
                    break
            if matched is None:
                # Fundamental/profile classification must come from a warehouse
                # profile view. Until that lane is populated, retain only
                # verified static membership and fail closed on fuzzy matching.
                matched=None
            if not matched:
                continue

            score=matched["score"]; confidence=float(matched["confidence"])
            # Only medium/high-confidence classification earns a ranking bonus.
            bonus=0.0
            if confidence>=0.70:
                bonus=max(0.0,min(THEME_BONUS_MAX,(score-50)/50*THEME_BONUS_MAX))*confidence

            # Summary-only matches are retained as diagnostics but are not
            # presented as an actual theme classification.
            if confidence>=0.70:
                out.at[idx,"theme"]=matched["theme"]
                out.at[idx,"theme_etf"]=matched["etf"]
                out.at[idx,"theme_score"]=round(score,1)
                out.at[idx,"theme_state"]=matched["state"]
                out.at[idx,"theme_bonus"]=round(bonus,2)
            out.at[idx,"theme_match_confidence"]=round(confidence,2)
            out.at[idx,"theme_match_source"]=matched["source"]
            out.at[idx,"theme_match_reason"]=matched["reason"]
        except Exception:
            pass
        time.sleep(0.03)

    return out
