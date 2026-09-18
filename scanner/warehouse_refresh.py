from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .warehouse import update

def _symbols() -> list[str]:
    paths=[OUTPUT_DIR/"tradable_universe.csv", Path("data/universe.csv")]
    for path in paths:
        if not path.exists() or not path.stat().st_size:
            continue
        df=pd.read_csv(path)
        for col in ("ticker","symbol","Ticker","Symbol"):
            if col in df.columns:
                vals=df[col].dropna().astype(str).str.upper().str.strip()
                vals=[x for x in vals if x]
                if vals:
                    return list(dict.fromkeys(vals))
    raise RuntimeError("WAREHOUSE_REFRESH_FAILED: no universe catalogue available")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--period",default="1y")
    p.add_argument("--interval",default="1d")
    p.add_argument("--limit",type=int,default=0)
    args=p.parse_args()
    tickers=_symbols()
    if args.limit>0:
        tickers=tickers[:args.limit]
    snap=update(tickers,period=args.period,interval=args.interval)
    print(f"WAREHOUSE_AVAILABLE run_id={snap.run_id} updated_at_utc={snap.updated_at_utc} interval={snap.interval} symbols={snap.symbols} rows={snap.rows}")

if __name__=="__main__":
    main()
