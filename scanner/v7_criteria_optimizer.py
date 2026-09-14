from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import OUTPUT_DIR
from .v4.evidence import training_outcomes
from .v7.criteria_optimizer import fit_criteria_optimizer


def run(state_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    proposal, validation, grid = fit_criteria_optimizer(training_outcomes(state_dir, output_dir))
    serialized = json.dumps(proposal, indent=2, sort_keys=True, allow_nan=False)
    (state_dir / "v7_2_criteria_proposal.json").write_text(serialized)
    (output_dir / "v7_2_criteria_proposal.json").write_text(serialized)
    validation.to_csv(output_dir / "v7_2_criteria_validation.csv", index=False)
    grid.to_csv(output_dir / "v7_2_criteria_grid.csv", index=False)
    print(
        f"V7.2 criteria optimizer: status={proposal['status']} "
        f"version={proposal['model_version']} samples={proposal['samples']}"
    )
    return proposal, validation, grid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate bounded V7.2 criteria proposals in shadow mode")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir))
