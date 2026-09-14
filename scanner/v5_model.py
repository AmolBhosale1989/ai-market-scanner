from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v4.source import HttpCandidateSource
from .v4.evidence import training_outcomes
from .v5.adaptive import fit_adaptive_model, save_model


def _outcomes(state_dir: Path, output_dir: Path) -> pd.DataFrame:
    return training_outcomes(state_dir, output_dir)


def run(state_dir: Path, output_dir: Path):
    model, validation = fit_adaptive_model(_outcomes(state_dir, output_dir))
    save_model(model, state_dir / "v5_model.json")
    save_model(model, output_dir / "v5_model.json")
    validation.to_csv(output_dir / "v5_validation.csv", index=False)
    try:
        candidates = HttpCandidateSource().load().frame
        ranked = model.score(candidates)
    except Exception:
        ranked = pd.DataFrame()
    ranked.to_csv(output_dir / "v5_ranked_candidates.csv", index=False)
    print(f"V5 adaptive model: status={model.status} version={model.version} samples={model.payload.get('samples', 0)}")
    return model, validation, ranked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit V5 regime-adaptive shadow ranking")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir))
