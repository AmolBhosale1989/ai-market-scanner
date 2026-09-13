from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v7.allocator import AllocationSettings, allocate_paper_portfolio


def _json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text())
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def run(output_dir: Path, paper_equity: float = 100_000.0):
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = AllocationSettings(paper_equity=paper_equity)
    allocations, health = allocate_paper_portfolio(
        _csv(output_dir / "v6_ranked_candidates.csv"),
        _json(output_dir / "v6_model.json"),
        settings,
    )
    allocations.to_csv(output_dir / "v7_paper_portfolio.csv", index=False)
    (output_dir / "v7_allocation_health.json").write_text(
        json.dumps(health, indent=2, sort_keys=True, allow_nan=False)
    )
    print(f"V7 paper allocator: status={health['status']} positions={health['positions']} risk={health.get('planned_total_risk_pct', 0)}%")
    return allocations, health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build V7 portfolio-aware paper allocation plan")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--paper-equity", type=float, default=100_000.0)
    args = parser.parse_args()
    run(Path(args.output_dir), args.paper_equity)
