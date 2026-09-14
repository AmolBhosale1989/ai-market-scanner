from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import OUTPUT_DIR
from .v4.shadow_validation import ShadowValidationLedger
from .v7.challenger import evaluate_challenger


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def run(state_dir: Path, output_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "v7_3_challenger_state.json"
    proposal = _json(output_dir / "v7_2_criteria_proposal.json") or _json(state_dir / "v7_2_criteria_proposal.json")
    observations = ShadowValidationLedger(state_dir / "v4_shadow_observations.json").load_frame()
    state, comparison = evaluate_challenger(proposal, observations, _json(state_file))
    serialized = json.dumps(state, indent=2, sort_keys=True, allow_nan=False)
    state_file.write_text(serialized)
    (output_dir / "v7_3_challenger_health.json").write_text(serialized)
    comparison.to_csv(output_dir / "v7_3_challenger_comparison.csv", index=False)
    print(
        f"V7.3 challenger: status={state['status']} "
        f"future_sessions={state.get('future_sessions', 0)} samples={state.get('baseline_samples', 0)}"
    )
    return state, comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run future-only V7.3 challenger validation")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir))
