from __future__ import annotations

import argparse
import os

import requests

from .control_plane import fail_run, publish, seed_from_publication


REQUIRED_DATASETS = (
    "warehouse_snapshot", "warehouse_coverage", "master_universe", "master_universe_health",
    "tradable_universe", "live_universe", "scan_health", "all_candidates", "latest_scan",
    "recommended_trades", "liquid_leaders", "watchlist", "trending_themes", "upcoming_events",
    "event_status", "product_feed", "legendary_setups", "legendary_consensus",
    "v3_live_discovery", "v3_live_snapshot", "intraday_live", "monitor_health",
    "state_transitions", "paper_journal", "performance_summary", "performance_by_setup",
    "probability_calibration", "validation_gate", "theme_health", "sector_rotation",
    "sector_rotation_health", "rotation_leaders", "momentum_signals", "momentum_health",
    "broad_breakout_discovery", "broad_breakout_health",
    "order_flow_strategy", "order_flow_strategy_health", "order_flow_strategy_journal",
    "order_flow_strategy_performance", "live_system_health", "live_system_health_summary",
    "daily_top_pick_log", "daily_top_pick_summary", "high_conviction_alert_log",
    "high_conviction_alert_health", "premarket_discovery", "premarket_health",
    "v31_challenger_log", "v31_challenger_summary", "v31_challenger_decisions",
    "quant_shadow_signals", "quant_shadow_ledger", "quant_shadow_performance", "quant_shadow_health",
    "v4_monitor_shortlist", "v4_live_snapshot", "v4_transitions", "v4_worker_cycles",
    "v4_worker_health", "v4_outcomes", "v4_outcome_summary", "v4_shadow_observations",
    "v4_options_microstructure", "v4_options_microstructure_health",
    "v4_catalyst_events", "v4_catalyst_health",
    "v4_shadow_strategy_summary", "v4_shadow_daily_comparison", "v4_shadow_breakdowns",
    "v4_shadow_validation_health", "v4_5_model", "v4_5_validation", "v4_5_ranked_candidates",
    "v4_model_monitor", "v4_6_cutover_evaluation", "v5_model", "v5_validation",
    "v5_ranked_candidates", "v6_model", "v6_validation", "v6_ranked_candidates",
    "v7_paper_portfolio", "v7_allocation_health", "v7_1_evidence_health",
    "v7_2_criteria_proposal", "v7_2_criteria_validation", "v7_2_criteria_grid",
    "v7_3_challenger_health", "v7_3_challenger_comparison", "social_content_queue",
    "social_content_calendar", "social_engine_health", "v8_1_operational_health",
    "v8_2_evidence_scorecard", "v9_readiness", "v9_1_pilot_candidates", "v9_1_pilot_health",
    "trader_minervini", "trader_oneil", "trader_weinstein", "trader_darvas",
    "trader_livermore", "trader_qullamaggie", "trader_druckenmiller", "trader_lawwaisum",
    "trader_martinluk", "trader_top500swing", "trader_highmomentumbeta", "trader_superstock",
    "trader_brownmoose", "trader_venu",
)


def finalize(mode: str = "production") -> dict:
    return publish(mode, REQUIRED_DATASETS)


def seed(mode: str = "production") -> int:
    return seed_from_publication(mode, REQUIRED_DATASETS)


def record_failure(stage: str, message: str) -> dict:
    fail_run(f"{stage}: {message}")
    payload = {"status": "FAILED", "stage": stage, "message": message}
    print(f"::error title=Market Hunt production failure::{stage}: {message}")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": f"Market Hunt FAILED\nStage: {stage}\n{message}"},
                timeout=15,
            )
            payload["telegram_sent"] = bool(response.ok)
        except Exception as exc:
            payload["telegram_sent"] = False
            payload["telegram_error"] = type(exc).__name__
    return payload


def main():
    parser = argparse.ArgumentParser(description="Atomic PostgreSQL publication and failure telemetry")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--mode", default="production")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--failure-stage")
    parser.add_argument("--message", default="Workflow failed; inspect the first failing dependency stage.")
    args = parser.parse_args()
    if args.finalize:
        result = finalize(args.mode)
        print(f"PUBLICATION_GATE_PASS run_id={result['pipeline_run_id']} datasets={len(result['datasets'])}")
    elif args.seed:
        print(f"CONTROL_PLANE_SEED_PASS datasets={seed(args.mode)}")
    elif args.failure_stage:
        record_failure(args.failure_stage, args.message)
    else:
        parser.error("specify --finalize or --failure-stage")


if __name__ == "__main__":
    main()
