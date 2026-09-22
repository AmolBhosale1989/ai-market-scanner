from __future__ import annotations

from .control_plane import append_state, read_record, read_state, write_dataset, write_record
from .v4.shadow_validation import ShadowValidationLedger
from .v7.challenger import evaluate_challenger


def run():
    proposal = read_record("v7_2_criteria_proposal", required=False)
    previous = read_state("v7", "challenger_state", default={}) or {}
    state, comparison = evaluate_challenger(proposal, ShadowValidationLedger().load_frame(), previous)
    append_state("v7", "challenger_state", state)
    write_record("v7_3_challenger_health", state)
    write_dataset("v7_3_challenger_comparison", comparison, entity_key=None)
    print(f"V7.3 challenger: status={state['status']} samples={state.get('baseline_samples', 0)}")
    return state, comparison


if __name__ == "__main__":
    run()
