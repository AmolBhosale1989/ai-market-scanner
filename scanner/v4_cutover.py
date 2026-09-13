from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v4.cutover import (
    CutoverController,
    CutoverSettings,
    evaluate_cutover,
)


def _csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def evaluate(state_dir: Path, output_dir: Path):
    decision = evaluate_cutover(
        _csv(output_dir / "all_candidates.csv"),
        _csv(output_dir / "v4_5_ranked_candidates.csv"),
        _csv(output_dir / "v4_outcomes.csv"),
        _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json"),
        _json(output_dir / "v4_worker_health.json"),
        CutoverSettings(),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v4_6_cutover_evaluation.json").write_text(
        json.dumps(decision.to_dict(), indent=2, sort_keys=True, allow_nan=False)
    )
    print(json.dumps(decision.to_dict(), indent=2))
    return decision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V4.6 production cutover controller")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--rollback", default="")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    output_dir = Path(args.output_dir)
    controller = CutoverController(state_dir / "v4_6_cutover_state.json")

    if args.rollback:
        print(json.dumps(controller.rollback(args.rollback), indent=2))
    elif args.activate:
        decision = evaluate(state_dir, output_dir)
        model = _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json")
        print(json.dumps(controller.activate(decision, str(model.get("model_version", ""))), indent=2))
    else:
        evaluate(state_dir, output_dir)
