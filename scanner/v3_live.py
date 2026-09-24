from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from .broad_breakout import run as run_broad_discovery
from .config import LIVE_ENRICH_LIMIT
from .intraday import run as run_intraday
from .v3_live_refresh import refresh_v3_candidates
from .control_plane import read_dataset, write_dataset


def _fresh_discovery(reuse_current_broad_discovery: bool = False) -> pd.DataFrame:
    """Build the V3 production candidate set from fresh PostgreSQL warehouse data.

    Persisted scan outputs are never accepted as production candidate inputs.
    The tradable-universe file is only a symbol/universe catalogue; the actual
    candidate qualification is recomputed from current intraday provider data.
    """
    discovered = (
        read_dataset("broad_breakout_discovery")
        if reuse_current_broad_discovery
        else run_broad_discovery(top_n=max(80, LIVE_ENRICH_LIMIT * 3))
    )
    if discovered is None or discovered.empty or "ticker" not in discovered.columns:
        raise RuntimeError(
            "V3 LIVE ABORTED: fresh PostgreSQL warehouse discovery returned no qualified candidates."
        )
    out = discovered.drop_duplicates("ticker").copy()
    out["v3_discovery_source"] = "POSTGRES_WAREHOUSE_DISCOVERY"
    out["v3_discovered_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_dataset("v3_live_discovery",out)
    return out


def run(input_file=None, limit=LIVE_ENRICH_LIMIT, reuse_current_broad_discovery: bool = False):
    """Production V3: PostgreSQL discovery -> warehouse refresh -> V3 decision.

    Persisted candidate artifacts are deliberately absent from the production path.
    input_file is retained only as a compatibility argument and is rejected so
    an old persisted scan cannot accidentally become a live V3 dependency.
    """
    if input_file:
        raise RuntimeError(
            "V3 LIVE ABORTED: persisted candidate files are disabled in production."
        )

    base = _fresh_discovery(reuse_current_broad_discovery=reuse_current_broad_discovery)
    refreshed = refresh_v3_candidates(base)
    write_dataset("v3_live_snapshot",refreshed)
    return run_intraday(input_frame=refreshed, limit=limit)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=LIVE_ENRICH_LIMIT)
    parser.add_argument(
        "--reuse-current-broad-discovery",
        action="store_true",
        help="Reuse the broad-breakout dataset produced earlier in this pipeline run.",
    )
    args = parser.parse_args()
    run(limit=args.limit, reuse_current_broad_discovery=args.reuse_current_broad_discovery)


if __name__ == "__main__":
    main()
