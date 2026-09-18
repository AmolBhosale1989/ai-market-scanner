from __future__ import annotations

import argparse
import math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from .warehouse import DataRequirement, provide

from .config import OUTPUT_DIR

NY = ZoneInfo("America/New_York")


def _truthy(v):
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def _normalize(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        if ticker in df.columns.get_level_values(0):
            out = df[ticker].copy()
        elif ticker in df.columns.get_level_values(-1):
            out = df.xs(ticker, axis=1, level=-1).copy()
        else:
            return pd.DataFrame()
    else:
        out = df.copy()
    needed = {"Close", "Volume"}
    if not needed.issubset(out.columns):
        return pd.DataFrame()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out.index = idx.tz_convert(NY)
    return out.dropna(subset=["Close"]).sort_index()


def _warehouse_batch(tickers: list[str], consumer: str, period: str="5d") -> dict[str,pd.DataFrame]:
    view=provide(DataRequirement(consumer=consumer,tickers=tuple(tickers),interval="5m",period=period,max_age_minutes=10,view_name=consumer+"_5m"))
    out={}
    for ticker,g in view.frame.groupby("ticker"):
        x=g.copy()
        x["bar_timestamp"]=pd.to_datetime(x["bar_timestamp"],utc=True,errors="coerce")
        out[str(ticker)]=x.dropna(subset=["bar_timestamp"]).set_index("bar_timestamp")
    return out

def run(input_file: str | None = None, batch_size: int = 80, top_n: int = 100):
    source = Path(input_file) if input_file else OUTPUT_DIR / "tradable_universe.csv"
    if not source.exists():
        print(f"No broad tradable universe found at {source}.")
        return pd.DataFrame()

    base = pd.read_csv(source)
    if base.empty or "ticker" not in base.columns:
        print("Tradable universe is empty.")
        return pd.DataFrame()

    if "tradable" in base.columns:
        base = base[base["tradable"].map(_truthy)].copy()
    base = base.drop_duplicates("ticker")
    tickers = base["ticker"].dropna().astype(str).tolist()
    prior_close_map = pd.to_numeric(base.set_index("ticker").get("price"), errors="coerce").to_dict()
    avg_dollar_map = pd.to_numeric(base.set_index("ticker").get("avg_dollar_volume20"), errors="coerce").to_dict()
    name_map = base.set_index("ticker").get("name", pd.Series(dtype=object)).to_dict()
    exchange_map = base.set_index("ticker").get("exchange", pd.Series(dtype=object)).to_dict()

    now_et = datetime.now(NY)
    rows = []
    batches = math.ceil(len(tickers) / batch_size) if tickers else 0
    print(f"Broad premarket discovery: scanning {len(tickers):,} tradable stocks in {batches} batches...")

    for bi, start in enumerate(range(0, len(tickers), batch_size), 1):
        batch = tickers[start:start + batch_size]
        print(f"Premarket batch {bi}/{batches}: {batch[0]} ... {batch[-1]}")
        try:
            raw = _warehouse_batch(batch,"premarket",period="1d")
        except Exception as exc:
            print(f"Batch failed: {exc}")
            continue
        if raw is None or raw.empty:
            continue

        for ticker in batch:
            d = _normalize(raw.get(ticker,pd.DataFrame()), ticker)
            if d.empty:
                continue
            today = d[d.index.date == now_et.date()]
            if today.empty:
                continue
            pm = today.between_time("04:00", "09:29")
            if pm.empty:
                continue

            price = float(pm["Close"].iloc[-1])
            volume = float(pd.to_numeric(pm["Volume"], errors="coerce").fillna(0).sum())
            prior_close = float(prior_close_map.get(ticker, math.nan))
            gap = (price / prior_close - 1) * 100 if math.isfinite(prior_close) and prior_close > 0 else math.nan
            pm_dollar_volume = price * volume if math.isfinite(price) else math.nan

            rows.append({
                "ticker": ticker,
                "company_name": name_map.get(ticker, ""),
                "exchange": exchange_map.get(ticker, ""),
                "prior_close": round(prior_close, 2) if math.isfinite(prior_close) else math.nan,
                "premarket_price": round(price, 2),
                "premarket_gap_pct": round(gap, 2) if math.isfinite(gap) else math.nan,
                "premarket_volume": round(volume, 0),
                "premarket_dollar_volume": round(pm_dollar_volume, 0) if math.isfinite(pm_dollar_volume) else math.nan,
                "avg_dollar_volume20": round(float(avg_dollar_map.get(ticker, 0) or 0), 0),
                "premarket_last_bar_et": pm.index[-1].isoformat(),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        out = pd.DataFrame(columns=[
            "ticker", "company_name", "exchange", "prior_close", "premarket_price",
            "premarket_gap_pct", "premarket_volume", "premarket_dollar_volume",
            "avg_dollar_volume20", "premarket_last_bar_et", "premarket_rank"
        ])
    else:
        # Rank bullish movers by a blend of gap and real premarket participation.
        gap = pd.to_numeric(out["premarket_gap_pct"], errors="coerce").fillna(-99)
        pm_dv = pd.to_numeric(out["premarket_dollar_volume"], errors="coerce").fillna(0)
        out["premarket_score"] = (gap.clip(-10, 25) * 4 + (pm_dv.clip(lower=0).map(lambda x: math.log10(x + 1)) * 6)).round(1)
        out = out.sort_values(
            ["premarket_score", "premarket_gap_pct", "premarket_dollar_volume"],
            ascending=[False, False, False]
        ).reset_index(drop=True)
        out["premarket_rank"] = range(1, len(out) + 1)
        out = out.head(top_n).copy()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_DIR / "premarket_discovery.csv", index=False)

    pd.DataFrame([{
        "checked_at_et": now_et.isoformat(timespec="seconds"),
        "universe_source": str(source),
        "tradable_symbols_scanned": len(tickers),
        "symbols_with_premarket_data": len(rows),
        "published_rows": len(out),
        "scope": "BROAD_TRADABLE_UNIVERSE",
    }]).to_csv(OUTPUT_DIR / "premarket_health.csv", index=False)

    print(f"Premarket discovery complete: {len(rows):,} symbols had premarket data; published top {len(out):,}.")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Market Hunt broad-universe premarket discovery")
    p.add_argument("--input", default=None)
    p.add_argument("--batch-size", type=int, default=80)
    p.add_argument("--top-n", type=int, default=100)
    args = p.parse_args()
    run(input_file=args.input, batch_size=args.batch_size, top_n=args.top_n)
