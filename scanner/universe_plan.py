from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR


def select_live_universe(frame: pd.DataFrame, limit: int = 420) -> pd.DataFrame:
    if frame.empty or "ticker" not in frame.columns:
        raise RuntimeError("LIVE_UNIVERSE_INVALID: tradable universe is empty")
    x=frame.copy()
    for col in ("avg_dollar_volume20","adr20_pct","max_up_day_30d_pct","ret20_pct"):
        if col not in x.columns:
            x[col]=0.0
        x[col]=pd.to_numeric(x[col],errors="coerce").fillna(0)
    x["live_priority"]=(
        x["avg_dollar_volume20"].rank(pct=True)*0.45
        + x["adr20_pct"].rank(pct=True)*0.20
        + x["max_up_day_30d_pct"].rank(pct=True)*0.25
        + x["ret20_pct"].rank(pct=True)*0.10
    )
    out=(x.sort_values(["live_priority","avg_dollar_volume20","ticker"],ascending=[False,False,True])
           .drop_duplicates("ticker").head(limit).reset_index(drop=True))
    if len(out)<min(limit,len(frame)):
        raise RuntimeError(f"LIVE_UNIVERSE_INCOMPLETE: got={len(out)} requested={limit}")
    return out


def main():
    p=argparse.ArgumentParser(description="Build the shared live warehouse/discovery universe")
    p.add_argument("--input",type=Path,default=OUTPUT_DIR/"tradable_universe.csv")
    p.add_argument("--output",type=Path,default=OUTPUT_DIR/"live_universe.csv")
    p.add_argument("--limit",type=int,default=420)
    args=p.parse_args()
    out=select_live_universe(pd.read_csv(args.input),limit=args.limit)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    out.to_csv(args.output,index=False)
    print(f"LIVE_UNIVERSE_AVAILABLE symbols={len(out)}")


if __name__ == "__main__":
    main()
