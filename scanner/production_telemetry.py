from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import OUTPUT_DIR


REQUIRED_ARTIFACTS={
    "daily":(
        "warehouse_snapshot.json","warehouse_coverage.csv","master_universe.csv",
        "tradable_universe.csv","scan_health.csv","all_candidates.csv","product_feed.json",
        "quant_shadow_signals.csv","quant_shadow_ledger.json",
        "quant_shadow_performance.csv","quant_shadow_health.json",
    ),
    "live":(
        "warehouse_snapshot.json","warehouse_coverage.csv","live_universe.csv",
        "v3_live_discovery.csv","v3_live_snapshot.csv","intraday_live.csv","monitor_health.csv",
        "theme_health.csv","sector_rotation_health.csv","momentum_health.csv",
        "order_flow_strategy.csv","order_flow_strategy_health.csv",
        "live_system_health_summary.csv","product_feed.json",
    ),
    "order-flow":(
        "warehouse_snapshot.json","warehouse_coverage.csv","live_universe.csv",
        "theme_health.csv","sector_rotation_health.csv","broad_breakout_health.csv",
        "momentum_health.csv","order_flow_strategy.csv","order_flow_strategy_health.csv",
    ),
}


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def finalize(mode: str, output_dir: Path = OUTPUT_DIR) -> dict:
    required=REQUIRED_ARTIFACTS[mode]
    missing=[name for name in required if not (output_dir/name).exists() or not (output_dir/name).stat().st_size]
    if missing:
        raise RuntimeError("PUBLICATION_GATE_FAILED: missing="+",".join(missing))
    snapshot=json.loads((output_dir/"warehouse_snapshot.json").read_text())
    if snapshot.get("status")!="PASS":
        raise RuntimeError("PUBLICATION_GATE_FAILED: warehouse snapshot did not pass")
    manifest={
        "schema_version":1,"mode":mode,"status":"PASS",
        "production_run_id":snapshot["production_run_id"],
        "warehouse_as_of_utc":snapshot["as_of_utc"],
        "source_commit":os.getenv("GITHUB_SHA","") or os.getenv("SOURCE_COMMIT","LOCAL"),
        "workflow_run_id":os.getenv("GITHUB_RUN_ID","") or "LOCAL",
        "finalized_at_utc":datetime.now(timezone.utc).isoformat(),
        "artifacts":[{"name":name,"bytes":(output_dir/name).stat().st_size,"sha256":_sha256(output_dir/name)} for name in required],
    }
    (output_dir/"production_publication_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True))
    return manifest


def record_failure(stage: str, message: str, output_dir: Path = OUTPUT_DIR) -> dict:
    payload={
        "status":"FAILED","stage":stage,"message":message,
        "workflow":os.getenv("GITHUB_WORKFLOW",""),"workflow_run_id":os.getenv("GITHUB_RUN_ID",""),
        "source_commit":os.getenv("GITHUB_SHA",""),"failed_at_utc":datetime.now(timezone.utc).isoformat(),
    }
    output_dir.mkdir(parents=True,exist_ok=True)
    (output_dir/"production_failure.json").write_text(json.dumps(payload,indent=2,sort_keys=True))
    print(f"::error title=Market Hunt production failure::{stage}: {message}")
    token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
    chat=os.getenv("TELEGRAM_CHAT_ID","").strip()
    if token and chat:
        try:
            response=requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id":chat,"text":f"Market Hunt FAILED\nStage: {stage}\n{message}"},timeout=15,
            )
            payload["telegram_sent"]=bool(response.ok)
        except Exception as exc:
            payload["telegram_sent"]=False
            payload["telegram_error"]=type(exc).__name__
        (output_dir/"production_failure.json").write_text(json.dumps(payload,indent=2,sort_keys=True))
    return payload


def main():
    p=argparse.ArgumentParser(description="Production publication telemetry and failure alerts")
    p.add_argument("--finalize",choices=sorted(REQUIRED_ARTIFACTS))
    p.add_argument("--failure-stage")
    p.add_argument("--message",default="Workflow failed; inspect the first failing dependency stage.")
    args=p.parse_args()
    if args.finalize:
        result=finalize(args.finalize)
        print(f"PUBLICATION_GATE_PASS run_id={result['production_run_id']} artifacts={len(result['artifacts'])}")
    elif args.failure_stage:
        record_failure(args.failure_stage,args.message)
    else:
        p.error("specify --finalize or --failure-stage")


if __name__ == "__main__":
    main()
