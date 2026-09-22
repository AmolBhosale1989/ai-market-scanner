from __future__ import annotations

from .control_plane import append_state, write_dataset, write_record
from .v4.evidence import training_outcomes
from .v4.source import ControlPlaneCandidateSource
from .v5.adaptive import fit_adaptive_model


def run():
    model, validation = fit_adaptive_model(training_outcomes())
    append_state("v4_models", "v5_model", model.payload)
    write_record("v5_model", model.payload)
    write_dataset("v5_validation", validation, entity_key=None)
    ranked = model.score(ControlPlaneCandidateSource().load().frame)
    write_dataset("v5_ranked_candidates", ranked, entity_key="ticker")
    print(f"V5 adaptive model: status={model.status} version={model.version} samples={model.payload.get('samples', 0)}")
    return model, validation, ranked


if __name__ == "__main__":
    run()
