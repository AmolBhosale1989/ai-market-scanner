from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v4.model_monitor import evaluate_model_health


def _csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run(state_dir: Path, output_dir: Path) -> dict:
    health = evaluate_model_health(
        _csv(output_dir / "v4_shadow_observations.csv"),
        _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json"),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v4_model_monitor.json").write_text(
        json.dumps(health, indent=2, sort_keys=True, allow_nan=False)
    )
    pd.DataFrame([{key: value for key, value in health.items() if key != "settings"}]).to_csv(
        output_dir / "v4_model_monitor.csv", index=False
    )
    print(json.dumps(health, indent=2))
    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor live V4.5 shadow calibration and expectancy")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir))
