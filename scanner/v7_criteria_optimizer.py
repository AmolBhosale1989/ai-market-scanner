from __future__ import annotations

from .control_plane import append_state, write_dataset, write_record
from .v4.evidence import training_outcomes
from .v7.criteria_optimizer import fit_criteria_optimizer


def run():
    proposal, validation, grid = fit_criteria_optimizer(training_outcomes())
    append_state("v7", "criteria_proposal", proposal)
    write_record("v7_2_criteria_proposal", proposal)
    write_dataset("v7_2_criteria_validation", validation, entity_key=None)
    write_dataset("v7_2_criteria_grid", grid, entity_key=None)
    print(f"V7.2 criteria optimizer: status={proposal['status']} samples={proposal['samples']}")
    return proposal, validation, grid


if __name__ == "__main__":
    run()
