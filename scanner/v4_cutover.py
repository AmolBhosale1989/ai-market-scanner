from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v4.source import HttpCandidateSource
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


def _v3_candidates(output_dir: Path) -> pd.DataFrame:
    local = _csv(output_dir / "all_candidates.csv")
    if not local.empty:
        return local
    try:
        return HttpCandidateSource().load().frame
    except Exception:
        return pd.DataFrame()


def evaluate(state_dir: Path, output_dir: Path):
    worker_health = _json(output_dir / "v4_worker_health.json") or _json(state_dir / "v4_worker_health.json")
    model_health = _json(output_dir / "v4_model_monitor.json")
    worker_health["model_monitor_status"] = str(model_health.get("status", "UNKNOWN"))
    decision = evaluate_cutover(
        _v3_candidates(output_dir),
        _csv(output_dir / "v4_5_ranked_candidates.csv"),
        _csv(output_dir / "v4_outcomes.csv"),
        _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json"),
        worker_health,
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
    parser.add_argument("--enforce-safety", action="store_true")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    output_dir = Path(args.output_dir)
    controller = CutoverController(state_dir / "v4_6_cutover_state.json")

    if args.rollback:
        print(json.dumps(controller.rollback(args.rollback), indent=2))
    elif args.activate:
        decision = evaluate(state_dir, output_dir)
        model = _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json")
        active = controller.activate(decision, str(model.get("model_version", "")))
        (state_dir / "v4_5_active_model.json").write_text(
            json.dumps(model, indent=2, sort_keys=True, allow_nan=False)
        )
        print(json.dumps(active, indent=2))
    else:
        decision = evaluate(state_dir, output_dir)
        if args.enforce_safety and controller.state().get("mode") == "V4_5_PRIMARY" and not decision.eligible:
            print(json.dumps(controller.rollback("automatic safety gate: " + ",".join(decision.failed_gates)), indent=2))
