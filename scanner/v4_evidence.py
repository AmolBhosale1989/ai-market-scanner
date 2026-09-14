from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import OUTPUT_DIR
from .v4.evidence import evaluate_evidence_health, import_durable_evidence
from .v4.shadow_validation import ShadowValidationLedger


def write_health(state_dir: Path, output_dir: Path) -> dict:
    ledger = ShadowValidationLedger(state_dir / "v4_shadow_observations.json")
    health = evaluate_evidence_health(ledger.load_frame())
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "v7_1_evidence_health.json").write_text(
        json.dumps(health, indent=2, sort_keys=True, allow_nan=False)
    )
    print(json.dumps(health, indent=2))
    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Restore and monitor durable V7.1 evidence ledgers")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--import-dir", default="")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    state_dir, output_dir = Path(args.state_dir), Path(args.output_dir)
    if args.import_dir:
        print(json.dumps(import_durable_evidence(state_dir, Path(args.import_dir)), indent=2))
    if args.health:
        write_health(state_dir, output_dir)
