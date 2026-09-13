from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .live import enrich_live_candidates
from .v4.engine import MomentumEngine
from .v4.shortlist import build_monitor_shortlist, load_shortlist_source
from .v4.store import FileEventStore


def run(
    input_file: str | None = None,
    hot_limit: int = 20,
    warm_limit: int = 80,
    fetch_limit: int = 20,
    state_dir: str = ".state/v4",
):
    source = pd.read_csv(input_file) if input_file else load_shortlist_source(OUTPUT_DIR)
    shortlist = build_monitor_shortlist(source, hot_limit=hot_limit, warm_limit=warm_limit)
    shortlist.to_csv(OUTPUT_DIR / "v4_monitor_shortlist.csv", index=False)

    # The free Yahoo adapter remains intentionally bounded. A streaming adapter
    # can later emit the same MarketEvent contract without changing the engine.
    live = enrich_live_candidates(shortlist, limit=min(fetch_limit, len(shortlist)))
    observed = datetime.now(timezone.utc).isoformat()
    engine = MomentumEngine(FileEventStore(Path(state_dir)))
    events = [engine.snapshot_event(row, observed) for row in live.to_dict(orient="records")]
    transitions = engine.process_many(events)

    live.to_csv(OUTPUT_DIR / "v4_live_snapshot.csv", index=False)
    transition_frame = pd.DataFrame([item.to_dict() for item in transitions])
    transition_frame.to_csv(OUTPUT_DIR / "v4_transitions.csv", index=False)
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
    parser.add_argument("--state-dir", default=".state/v4")
    args = parser.parse_args()
    run(
        input_file=args.input,
        hot_limit=args.hot_limit,
        warm_limit=args.warm_limit,
        fetch_limit=args.fetch_limit,
        state_dir=args.state_dir,
    )
