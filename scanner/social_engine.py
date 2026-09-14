from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .social.engine import build_social_content


def _csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def run(input_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    queue, calendar, health = build_social_content(
        _csv(input_dir / "scan_health.csv"),
        _csv(input_dir / "all_candidates.csv"),
        _csv(input_dir / "recommended_trades.csv"),
        _csv(input_dir / "trending_themes.csv"),
    )
    queue.to_csv(output_dir / "social_content_queue.csv", index=False)
    calendar.to_csv(output_dir / "social_content_calendar.csv", index=False)
    (output_dir / "social_engine_health.json").write_text(
        json.dumps(health, indent=2, sort_keys=True, allow_nan=False)
    )
    print(
        f"Social engine: status={health['status']} drafts={health['drafts_generated']} "
        "external_actions=0"
    )
    return queue, calendar, health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate approval-first Market Hunt social drafts")
    parser.add_argument("--input-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.input_dir), Path(args.output_dir))
