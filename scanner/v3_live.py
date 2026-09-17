from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import LIVE_ENRICH_LIMIT, OUTPUT_DIR
from .intraday import run as run_intraday
from .v3_live_refresh import refresh_v3_candidates


def run(input_file=None, limit=LIVE_ENRICH_LIMIT):
    """Production V3 live path: provider -> refreshed V3 -> live decision.

    A persisted scan may nominate candidate identities, but its market/technical
    values are not trusted. They are recomputed from the provider before the
    intraday V3 decision engine is allowed to run.
    """
    source = Path(input_file) if input_file else OUTPUT_DIR / "latest_scan.csv"
    if not source.exists():
        raise RuntimeError(f"V3 LIVE ABORTED: candidate identity file missing: {source}")

    base = pd.read_csv(source)
    refreshed = refresh_v3_candidates(base)
    refreshed_path = OUTPUT_DIR / "v3_live_snapshot.csv"
    refreshed.to_csv(refreshed_path, index=False)

    # intraday.run now receives the just-refreshed snapshot, never the stale
    # market/technical values from latest_scan.csv.
    return run_intraday(input_file=str(refreshed_path), limit=limit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=None)
    parser.add_argument("--limit", type=int, default=LIVE_ENRICH_LIMIT)
    args = parser.parse_args()
    run(input_file=args.input, limit=args.limit)


if __name__ == "__main__":
    main()
