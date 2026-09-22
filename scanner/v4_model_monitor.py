from __future__ import annotations

import json

from .control_plane import read_dataset, read_state, write_record
from .v4.model_monitor import evaluate_model_health


def run() -> dict:
    health = evaluate_model_health(
        read_dataset("v4_shadow_observations", required=False),
        read_state("v4_models", "v4_5_model", default={}) or {},
    )
    write_record("v4_model_monitor", health)
    print(json.dumps(health, indent=2))
    return health


if __name__ == "__main__":
    run()
