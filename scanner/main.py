import argparse
import math
import numpy as np
import pandas as pd

from .config import OUTPUT_DIR, MIN_PRICE, MIN_AVG_DOLLAR_VOLUME, TOP_N, BATCH_SIZE, BENCHMARK
from .data import download_history, download_batch
from .indicators import add_indicators
from .stocks import analyze_dataframe
from .universe import load_or_build_universe

def _benchmark_return20():
    df = download_history(BENCHMARK, "3mo", "1d")
    if len(df) < 25:
        return 0.0
    d = add_indicators(df)
    return float(d.iloc[-1]["RET20"])

def _representative_sample(universe: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Evenly sample across the alphabetically sorted universe instead of only A names."""
    if limit <= 0 or limit >= len(universe):
        return universe.copy()
    idx = np.linspace(0, len(universe) - 1, num=limit, dtype=int)
    return universe.iloc[idx].reset_index(drop=True)

def run(refresh_universe: bool = False, limit: int | None = None, top_n: int = TOP_N):
    universe = load_or_build_universe(force_refresh=refresh_universe).sort_values("ticker").reset_index(drop=True)
    full_count = len(universe)
    if limit:
        universe = _representative_sample(universe, limit)
        print(f"Representative sample: {len(universe):,} of {full_count:,} symbols")
    tickers = universe["ticker"].dropna().astype(str).tolist()
    print(f"Universe used: {len(tickers):,} symbols")

    bench20 = _benchmark_return20()
    rows = []
    total_batches = math.ceil(len(tickers) / BATCH_SIZE)
    for bi, start in enumerate(range(0, len(tickers), BATCH_SIZE), 1):
        batch = tickers[start:start + BATCH_SIZE]
        print(f"Batch {bi}/{total_batches}: {batch[0]} ... {batch[-1]}")
        histories = download_batch(batch, period="1y", interval="1d")
        for ticker, hist in histories.items():
            try:
                result = analyze_dataframe(ticker, hist, benchmark_return20=bench20)
                if not result:
                    continue
                if result["price"] < MIN_PRICE:
                    continue
                if result["avg_dollar_volume"] < MIN_AVG_DOLLAR_VOLUME:
                    continue
                rows.append(result)
            except Exception as e:
                print(f"{ticker}: {e}")

    if not rows:
        print("No valid candidates found.")
        return None

    df = pd.DataFrame(rows)
    stage_rank = {"CONFIRMED": 5, "ARMED": 4, "FORMING": 3, "DISCOVER": 2, "EXTENDED": 1, "REJECT": 0}
    df["stage_rank"] = df["stage"].map(stage_rank).fillna(0)
    df["rank_score"] = (
        df["technical_score"] * 0.65
        + df["formation_score"] * 0.20
        + df["rs20_vs_spy"].clip(-20, 20) * 0.50
        - df["risk_score"] * 0.15
    ).round(1)

    all_out = OUTPUT_DIR / "all_candidates.csv"
    df.sort_values(["rank_score", "avg_dollar_volume"], ascending=[False, False]).to_csv(all_out, index=False)

    shortlist = df[df["stage"].isin(["CONFIRMED", "ARMED", "FORMING", "DISCOVER"])].copy()
    shortlist = shortlist.sort_values(
        ["stage_rank", "rank_score", "rr_to_8pct"], ascending=[False, False, False]
    ).head(top_n)
    shortlist = shortlist.drop(columns=["stage_rank"], errors="ignore")

    out = OUTPUT_DIR / "latest_scan.csv"
    shortlist.to_csv(out, index=False)

    print("\nTOP MARKET HUNT CANDIDATES")
    cols = ["ticker", "price", "stage", "pattern", "rank_score", "rs20_vs_spy", "rvol",
            "entry_trigger", "stop", "target_8", "rr_to_8pct", "runway_to_next_resistance_pct", "decision"]
    print(shortlist[cols].to_string(index=False))
    print(f"\nSaved shortlist: {out}")
    print(f"Saved all liquid candidates: {all_out}")
    return shortlist

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Market Hunt V3 broad U.S. scanner")
    p.add_argument("--refresh-universe", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--top", type=int, default=TOP_N)
    args = p.parse_args()
    run(refresh_universe=args.refresh_universe, limit=args.limit, top_n=args.top)
