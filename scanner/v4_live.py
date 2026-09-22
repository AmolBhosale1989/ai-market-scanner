from __future__ import annotations

import argparse
from datetime import datetime, timezone
import pandas as pd

from .control_plane import read_dataset, write_dataset
from .live import enrich_live_candidates
from .v4.engine import MomentumEngine
from .v4.shortlist import build_monitor_shortlist, load_shortlist_source
from .v4.store import PostgresEventStore


def run(
    input_file: str | None = None,
    hot_limit: int = 20,
    warm_limit: int = 80,
    fetch_limit: int = 20,
):
    if input_file:
        raise RuntimeError("FILE_INPUT_DISABLED: use all_candidates in the control plane")
    source = load_shortlist_source()
    shortlist = build_monitor_shortlist(source, hot_limit=hot_limit, warm_limit=warm_limit)
    write_dataset("v4_monitor_shortlist", shortlist, entity_key="ticker")

    # The free Yahoo adapter remains intentionally bounded. A streaming adapter
    # can later emit the same MarketEvent contract without changing the engine.
    live = enrich_live_candidates(shortlist, limit=min(fetch_limit, len(shortlist)))
    observed = datetime.now(timezone.utc).isoformat()
    engine = MomentumEngine(PostgresEventStore())
    events = [engine.snapshot_event(row, observed) for row in live.to_dict(orient="records")]
    transitions = engine.process_many(events)

    write_dataset("v4_live_snapshot", live, entity_key="ticker")
    transition_frame = pd.DataFrame([item.to_dict() for item in transitions])
    write_dataset("v4_transitions", transition_frame, entity_key="signal_id")
    print(
        f"V4 shadow monitor: shortlist={len(shortlist)}, fetched={min(fetch_limit, len(shortlist))}, "
        f"transitions={len(transitions)}"
    )
    return shortlist, live, transition_frame


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V4 shadow momentum engine")
    parser.add_argument("--input", default=None)
    parser.add_argument("--hot-limit", type=int, default=20)
    parser.add_argument("--warm-limit", type=int, default=80)
    parser.add_argument("--fetch-limit", type=int, default=20)
    args = parser.parse_args()
    run(
        input_file=args.input,
        hot_limit=args.hot_limit,
        warm_limit=args.warm_limit,
        fetch_limit=args.fetch_limit,
    )
