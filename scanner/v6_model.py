from __future__ import annotations

import pandas as pd

from .control_plane import append_state, write_dataset, write_record
from .v4.evidence import training_outcomes
from .v4.source import ControlPlaneCandidateSource
from .v6.uncertainty import fit_uncertainty_model


def run():
    model, validation = fit_uncertainty_model(training_outcomes())
    append_state("v4_models", "v6_model", model.payload)
    write_record("v6_model", model.payload)
    write_dataset("v6_validation", validation, entity_key=None)
    ranked = (model.score(ControlPlaneCandidateSource().load().frame)
              if model.status != "INSUFFICIENT_DATA" else pd.DataFrame())
    write_dataset("v6_ranked_candidates", ranked, entity_key="ticker")
    print(f"V6 uncertainty model: status={model.status} version={model.version} samples={model.payload.get('samples', 0)}")
    return model, validation, ranked


if __name__ == "__main__":
    run()
