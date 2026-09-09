import pandas as pd
from .config import UNIVERSE_FILE, OUTPUT_DIR, MIN_PRICE, MIN_AVG_DOLLAR_VOLUME, TOP_N
from .stocks import analyze_stock
from .sectors import sector_score

def run():
    universe = pd.read_csv(UNIVERSE_FILE)

    etfs = sorted(universe["sector_etf"].unique())
    sector_scores = {}
    for etf in etfs:
        try:
            sector_scores[etf] = sector_score(etf)
        except Exception:
            sector_scores[etf] = None

    rows = []
    for _, item in universe.iterrows():
        ticker = item["ticker"]
        try:
            result = analyze_stock(ticker)
            if not result:
                continue
            if result["price"] < MIN_PRICE:
                continue
            if result["avg_dollar_volume"] < MIN_AVG_DOLLAR_VOLUME:
                continue
            result["sector"] = item["sector"]
            result["sector_etf"] = item["sector_etf"]
            result["sector_score"] = sector_scores.get(item["sector_etf"])
            rows.append(result)
        except Exception as e:
            print(f"{ticker}: {e}")

    if not rows:
        print("No valid candidates found.")
        return

    df = pd.DataFrame(rows)
    df["combined_score"] = (
        df["opportunity_score"] * 0.8 +
        df["sector_score"].fillna(50) * 0.2
    ).round(1)

    df = df.sort_values(["combined_score", "risk_score"], ascending=[False, True]).head(TOP_N)
    out = OUTPUT_DIR / "latest_scan.csv"
    df.to_csv(out, index=False)
    print(df.to_string(index=False))
    print(f"\nSaved: {out}")

if __name__ == "__main__":
    run()
