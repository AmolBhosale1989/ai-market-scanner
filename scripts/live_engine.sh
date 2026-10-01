#!/usr/bin/env bash
# Engine-only lane: ingestion commits belong to the independent Feeder.
set -Eeuo pipefail
fail() {
  python -m scanner.production_telemetry --failure-stage "${CURRENT_STAGE:-engine}" --message "command failed at line $1"
}
trap 'fail $LINENO' ERR
stage() {
  CURRENT_STAGE="$1"
  local order="$2"
  shift 2
  python -m scanner.control_plane stage-begin --name "$CURRENT_STAGE" --order "$order"
  "$@"
  python -m scanner.control_plane stage-pass --name "$CURRENT_STAGE"
}
wait_stage() {
  local pid="$1"
  local name="$2"
  if ! wait "$pid"; then
    CURRENT_STAGE="$name"
    return 1
  fi
}

stage warehouse_gate 70 python -m scanner.warehouse_gate --require-feeder --tier MASTER_DAILY --tier CRITICAL_DAILY --tier CRITICAL_INTRADAY --tier THEME_INTRADAY --tier LIVE_INTRADAY
export WAREHOUSE_CONSUMER_SNAPSHOT=1
stage catalyst_gate 72 python -m scanner.catalyst_pipeline --verify-only

# The market-open lane has a ten-minute freshness contract. Launch
# independent consumers against the same validated warehouse
# snapshot, then join only where a dataset dependency requires it.
stage broad_breakout 110 python -m scanner.broad_breakout & broad_pid=$!
stage theme_live 90 python -m scanner.theme_live & theme_pid=$!
stage sector_rotation 100 python -m scanner.sector_rotation & sector_pid=$!
stage premarket 115 python -m scanner.premarket & premarket_pid=$!
stage v4_live 180 python -m scanner.v4_worker --once --options-microstructure & v4_pid=$!

wait_stage "$premarket_pid" premarket
wait_stage "$broad_pid" broad_breakout
stage v3_live 80 python -m scanner.v3_live --reuse-current-broad-discovery --defer-finalization & v3_pid=$!
wait_stage "$sector_pid" sector_rotation
stage momentum 120 python -m scanner.momentum_signals
stage order_flow 130 python -m scanner.order_flow_strategy
wait_stage "$v3_pid" v3_live
wait_stage "$theme_pid" theme_live
stage strategy_finalize 138 python -m scanner.strategy_finalize

stage order_flow_validation 140 python -m scanner.order_flow_validation & validation_pid=$!
stage v31_challenger 145 python -m scanner.v31_challenger & challenger_pid=$!
stage paper_performance 150 bash -c 'python -m scanner.performance && python -m scanner.readiness' & paper_pid=$!
stage daily_pick 155 python -m scanner.daily_pick & pick_pid=$!
stage high_conviction_alerts 158 python -m scanner.high_conviction_alerts & alerts_pid=$!

wait_stage "$validation_pid" order_flow_validation
wait_stage "$challenger_pid" v31_challenger
wait_stage "$paper_pid" paper_performance
wait_stage "$pick_pid" daily_pick
wait_stage "$alerts_pid" high_conviction_alerts
stage discovery_products 160 python -m scanner.product_feed
wait_stage "$v4_pid" v4_live

# Daily research, social and evidence-maturity artifacts are seeded
# from the last atomic full publication. The live lane updates only
# market-sensitive datasets before the final real-time freshness gate.
stage operational_health 270 python -m scanner.system_health --require-production

stage audit 280 python -m scanner.production_audit
stage acceptance 290 python -m scanner.production_acceptance
CURRENT_STAGE=publication
python -m scanner.production_telemetry --finalize --mode production
