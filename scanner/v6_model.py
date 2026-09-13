from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .v4.source import HttpCandidateSource
from .v6.uncertainty import fit_uncertainty_model, save_model


def _outcomes(state_dir: Path, output_dir: Path) -> pd.DataFrame:
    try:
        payload = json.loads((state_dir / "v4_outcomes.json").read_text())
        return pd.DataFrame((payload.get("signals") or {}).values())
    except (OSError, json.JSONDecodeError):
        try:
            return pd.read_csv(output_dir / "v4_outcomes.csv")
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            return pd.DataFrame()


def run(state_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    model, validation = fit_uncertainty_model(_outcomes(state_dir, output_dir))
    save_model(model, state_dir / "v6_model.json")
    save_model(model, output_dir / "v6_model.json")
    validation.to_csv(output_dir / "v6_validation.csv", index=False)
    ranked = pd.DataFrame()
    if model.status != "INSUFFICIENT_DATA":
        try:
            candidates = HttpCandidateSource().load().frame
            ranked = model.score(candidates)
        except Exception:
            ranked = pd.DataFrame()
    ranked.to_csv(output_dir / "v6_ranked_candidates.csv", index=False)
    print(f"V6 uncertainty model: status={model.status} version={model.version} samples={model.payload.get('samples', 0)}")
    return model, validation, ranked


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fit V6 uncertainty-aware shadow ensemble")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir))
