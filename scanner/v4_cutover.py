from __future__ import annotations

import argparse
import json

from .control_plane import append_state, read_dataset, read_record, read_state, write_record
from .v4.cutover import CutoverController, CutoverSettings, evaluate_cutover


def evaluate():
    worker_health = read_record("v4_worker_health", required=False)
    worker_health["model_monitor_status"] = str(read_record("v4_model_monitor", required=False).get("status", "UNKNOWN"))
    worker_health["evidence_health_status"] = str(read_record("v7_1_evidence_health", required=False).get("status", "UNKNOWN"))
    model = read_state("v4_models", "v4_5_model", default={}) or {}
    decision = evaluate_cutover(
        read_dataset("all_candidates"),
        read_dataset("v4_5_ranked_candidates", required=False),
        read_dataset("v4_outcomes", required=False),
        model,
        worker_health,
        CutoverSettings(),
    )
    write_record("v4_6_cutover_evaluation", decision.to_dict())
    print(json.dumps(decision.to_dict(), indent=2))
    return decision


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V4 production cutover controller")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--rollback", default="")
    parser.add_argument("--enforce-safety", action="store_true")
    args = parser.parse_args()
    controller = CutoverController()
    if args.rollback:
        print(json.dumps(controller.rollback(args.rollback), indent=2))
    elif args.activate:
        decision = evaluate()
        model = read_state("v4_models", "v4_5_model", default={}) or {}
        active = controller.activate(decision, str(model.get("model_version", "")))
        append_state("v4_models", "v4_5_active_model", model)
        print(json.dumps(active, indent=2))
    else:
        decision = evaluate()
        if args.enforce_safety and controller.state().get("mode") == "V4_5_PRIMARY" and not decision.eligible:
            print(json.dumps(controller.rollback("automatic safety gate: " + ",".join(decision.failed_gates)), indent=2))
