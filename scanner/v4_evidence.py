from __future__ import annotations

import argparse
import json

from .control_plane import write_record
from .v4.evidence import evaluate_evidence_health
from .v4.shadow_validation import ShadowValidationLedger


def write_health() -> dict:
    health = evaluate_evidence_health(ShadowValidationLedger().load_frame())
    write_record("v7_1_evidence_health", health)
    print(json.dumps(health, indent=2))
    return health


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monitor durable V7 evidence ledgers")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()
    if args.health:
        write_health()
