from io import StringIO
from pathlib import Path
import re
import requests
import pandas as pd
from .config import NASDAQ_LISTED_URL, OTHER_LISTED_URL, UNIVERSE_FILE
from .config import MASTER_UNIVERSE_BASELINE, MASTER_UNIVERSE_MAX_DRIFT, MASTER_UNIVERSE_MINIMUM, OUTPUT_DIR

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
    if len(u) < MASTER_UNIVERSE_MINIMUM:
        raise RuntimeError(
            f"MASTER_UNIVERSE_INCOMPLETE: got={len(u)} minimum={MASTER_UNIVERSE_MINIMUM}"
        )
    u.to_csv(output_file,index=False)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    u.to_csv(OUTPUT_DIR/"master_universe.csv", index=False)
    drift=len(u)-MASTER_UNIVERSE_BASELINE
    pd.DataFrame([{
        "master_symbols":len(u),
        "audited_baseline":MASTER_UNIVERSE_BASELINE,
        "drift":drift,
        "drift_within_limit":abs(drift)<=MASTER_UNIVERSE_MAX_DRIFT,
        "status":"PASS" if abs(drift)<=MASTER_UNIVERSE_MAX_DRIFT else "FAIL",
        "generated_at_utc":pd.Timestamp.now(tz="UTC").isoformat(),
    }]).to_csv(OUTPUT_DIR/"master_universe_health.csv", index=False)
    if abs(drift)>MASTER_UNIVERSE_MAX_DRIFT:
        raise RuntimeError(
            f"MASTER_UNIVERSE_DRIFT: got={len(u)} baseline={MASTER_UNIVERSE_BASELINE} "
            f"max_drift={MASTER_UNIVERSE_MAX_DRIFT}"
        )
    return u

def load_or_build_universe(force_refresh:bool=False)->pd.DataFrame:
    if force_refresh or not UNIVERSE_FILE.exists():
        return build_universe()
    u=pd.read_csv(UNIVERSE_FILE)
    if len(u)<500:
        return build_universe()
    return u


def main():
    import argparse
    p=argparse.ArgumentParser(description="Build the authoritative U.S. equity master universe")
    p.add_argument("--refresh", action="store_true")
    args=p.parse_args()
    u=load_or_build_universe(force_refresh=args.refresh)
    if len(u)<MASTER_UNIVERSE_MINIMUM:
        raise RuntimeError(f"MASTER_UNIVERSE_INCOMPLETE: {len(u)}")
    print(f"MASTER_UNIVERSE_AVAILABLE symbols={len(u)} baseline={MASTER_UNIVERSE_BASELINE}")


if __name__ == "__main__":
    main()
