from io import StringIO
from pathlib import Path
import re
import requests
import pandas as pd
from .config import NASDAQ_LISTED_URL, OTHER_LISTED_URL, UNIVERSE_FILE

HEADERS={"User-Agent":"Mozilla/5.0 ai-market-scanner/3.0"}

def _read_pipe(url:str)->pd.DataFrame:
    r=requests.get(url,headers=HEADERS,timeout=30)
    r.raise_for_status()
    return pd.read_csv(StringIO(r.text),sep="|")

def _clean_symbol(s:str):
    if not isinstance(s,str):
        return None
    s=s.strip().upper().replace(".","-")
    if not re.fullmatch(r"[A-Z0-9\-]{1,10}",s):
        return None
    # Yahoo-style warrant suffixes are not common-stock candidates.
    if s.endswith("-W"):
        return None
    return s

def build_universe(output_file:Path=UNIVERSE_FILE)->pd.DataFrame:
    nas=_read_pipe(NASDAQ_LISTED_URL)
    oth=_read_pipe(OTHER_LISTED_URL)

    nas=nas[nas["Symbol"].notna() & ~nas["Symbol"].astype(str).str.startswith("File Creation")]
    oth=oth[oth["ACT Symbol"].notna() & ~oth["ACT Symbol"].astype(str).str.startswith("File Creation")]

    if "Test Issue" in nas:
        nas=nas[nas["Test Issue"].astype(str).str.upper().eq("N")]
    if "ETF" in nas:
        nas=nas[nas["ETF"].astype(str).str.upper().eq("N")]
    if "Test Issue" in oth:
        oth=oth[oth["Test Issue"].astype(str).str.upper().eq("N")]
    if "ETF" in oth:
        oth=oth[oth["ETF"].astype(str).str.upper().eq("N")]

    a=pd.DataFrame({
        "ticker":nas["Symbol"].map(_clean_symbol),
        "name":nas.get("Security Name",""),
        "exchange":"NASDAQ",
    })
    exch_map={"N":"NYSE","A":"NYSE American","P":"NYSE Arca","Z":"BATS","V":"IEX"}
    b=pd.DataFrame({
        "ticker":oth["ACT Symbol"].map(_clean_symbol),
        "name":oth.get("Security Name",""),
        "exchange":oth.get("Exchange","").map(exch_map).fillna(oth.get("Exchange","")),
    })

    u=pd.concat([a,b],ignore_index=True).dropna(subset=["ticker"])

    # Exclude instruments that are not ordinary/common equity candidates.
    bad=r"\b(?:WARRANTS?|RIGHTS?|UNITS?|PREFERRED|PREF\.?|DEPOSITARY|DEPOSITORY|ETF|ETN|FUNDS?|NOTES?|BONDS?)\b"
    names=u["name"].astype(str).str.upper()
    u=u[~names.str.contains(bad,regex=True,na=False)]

    # Extra symbol-level cleanup for warrant/unit conventions that occasionally
    # slip through incomplete exchange descriptions.
    ticker=u["ticker"].astype(str)
    u=u[
        ~ticker.str.endswith("-W")
        & ~ticker.str.endswith("-U")
        & ~ticker.str.endswith("-R")
    ]

    u=u.drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)
    u.to_csv(output_file,index=False)
    return u

def load_or_build_universe(force_refresh:bool=False)->pd.DataFrame:
    if force_refresh or not UNIVERSE_FILE.exists():
        return build_universe()
    u=pd.read_csv(UNIVERSE_FILE)
    if len(u)<500:
        return build_universe()
    return u
