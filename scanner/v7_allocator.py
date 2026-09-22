from __future__ import annotations

import argparse

from .control_plane import read_dataset, read_record, write_dataset, write_record
from .v7.allocator import AllocationSettings, allocate_paper_portfolio


def run(paper_equity: float = 100_000.0):
    allocations, health = allocate_paper_portfolio(
        read_dataset("v6_ranked_candidates", required=False),
        read_record("v6_model", required=False),
        AllocationSettings(paper_equity=paper_equity),
    )
    write_dataset("v7_paper_portfolio", allocations, entity_key="ticker")
    write_record("v7_allocation_health", health)
    print(f"V7 paper allocator: status={health['status']} positions={health['positions']}")
    return allocations, health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build V7 paper allocation plan")
    parser.add_argument("--paper-equity", type=float, default=100_000.0)
    args = parser.parse_args()
    run(args.paper_equity)
