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
    Candidate qualification is recomputed from snapshot-bound warehouse prices.
    An available zero-row current-run dataset is a valid no-setups result;
    missing datasets and warehouse errors are never converted to that result.
    """
    discovered = (
        read_dataset("broad_breakout_discovery")
        if reuse_current_broad_discovery
        else run_broad_discovery(top_n=max(80, LIVE_ENRICH_LIMIT * 3))
    )
    if not isinstance(discovered, pd.DataFrame):
        raise RuntimeError("V3_DISCOVERY_INVALID: expected a current-run DataFrame")
    if discovered.empty:
        # Empty dataset rows lose their columns when read from PostgreSQL.
        # Restore the schema needed by the existing empty intraday path.
        out = pd.DataFrame(columns=["ticker", "stage"])
        print("V3_DISCOVERY_EMPTY: no qualified candidates; writing empty snapshot-bound outputs", flush=True)
    else:
        if "ticker" not in discovered or discovered["ticker"].isna().any():
            raise RuntimeError("V3_DISCOVERY_INVALID: missing candidate identities")
        if discovered["ticker"].astype(str).str.strip().eq("").any():
            raise RuntimeError("V3_DISCOVERY_INVALID: blank candidate identities")
        out = discovered.drop_duplicates("ticker").copy()
    out["v3_discovery_source"] = "POSTGRES_WAREHOUSE_DISCOVERY"
    out["v3_discovered_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_dataset("v3_live_discovery",out)
    return out


def run(input_file=None, limit=LIVE_ENRICH_LIMIT, reuse_current_broad_discovery: bool = False, *, defer_finalization: bool = False):
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
    refreshed = base.copy() if base.empty else refresh_v3_candidates(base)
    write_dataset("v3_live_snapshot",refreshed)
    kwargs = {"defer_finalization": True} if defer_finalization else {}
    return run_intraday(input_frame=refreshed, limit=limit, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=LIVE_ENRICH_LIMIT)
    parser.add_argument(
        "--reuse-current-broad-discovery",
        action="store_true",
        help="Reuse the broad-breakout dataset produced earlier in this pipeline run.",
    )
    parser.add_argument("--defer-finalization", action="store_true",
                        help="Write V3 candidates; leave recommendations to the production join")
    args = parser.parse_args()
    run(limit=args.limit, reuse_current_broad_discovery=args.reuse_current_broad_discovery,
        defer_finalization=args.defer_finalization)


if __name__ == "__main__":
    main()
