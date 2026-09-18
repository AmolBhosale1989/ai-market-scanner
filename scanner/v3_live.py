from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .broad_breakout import run as run_broad_discovery
from .config import LIVE_ENRICH_LIMIT, OUTPUT_DIR
from .intraday import run as run_intraday
from .v3_live_refresh import refresh_v3_candidates


def _fresh_discovery() -> pd.DataFrame:
    """Build the V3 production candidate set from fresh PostgreSQL warehouse data.

    Persisted scan outputs are never accepted as production candidate inputs.
    The tradable-universe file is only a symbol/universe catalogue; the actual
    candidate qualification is recomputed from current intraday provider data.
    """
    discovered = run_broad_discovery(top_n=max(80, LIVE_ENRICH_LIMIT * 3))
    if discovered is None or discovered.empty or "ticker" not in discovered.columns:
        raise RuntimeError(
            "V3 LIVE ABORTED: fresh PostgreSQL warehouse discovery returned no qualified candidates."
        )
    out = discovered.drop_duplicates("ticker").copy()
    out["v3_discovery_source"] = "POSTGRES_WAREHOUSE_DISCOVERY"
    out["v3_discovered_at_utc"] = datetime.now(timezone.utc).isoformat()
    out.to_csv(OUTPUT_DIR / "v3_live_discovery.csv", index=False)
    return out


def run(input_file=None, limit=LIVE_ENRICH_LIMIT):
    """Production V3: PostgreSQL discovery -> warehouse refresh -> V3 decision.

    Refactor 2 deliberately removes latest_scan.csv from the production path.
    input_file is retained only as a compatibility argument and is rejected so
    an old persisted scan cannot accidentally become a live V3 dependency.
    """
    if input_file:
        raise RuntimeError(
            "V3 LIVE ABORTED: persisted candidate files are disabled in production."
        )

    base = _fresh_discovery()
    refreshed = refresh_v3_candidates(base)
    refreshed_path = OUTPUT_DIR / "v3_live_snapshot.csv"
    refreshed.to_csv(refreshed_path, index=False)

    return run_intraday(input_file=str(refreshed_path), limit=limit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=LIVE_ENRICH_LIMIT)
    args = parser.parse_args()
    run(limit=args.limit)


if __name__ == "__main__":
    main()
