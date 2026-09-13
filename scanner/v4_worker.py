from __future__ import annotations

import argparse
from pathlib import Path

from .config import OUTPUT_DIR
from .v4.adapters import YahooPollingAdapter
from .v4.alerting import AlertRouter, FileAlertSink, TelegramAlertSink
from .v4.engine import MomentumEngine
from .v4.health import HealthRecorder
from .v4.source import FallbackCandidateSource, HttpCandidateSource, LocalCandidateSource
from .v4.store import FileEventStore
from .v4.worker import ContinuousMomentumWorker, WorkerSettings


def build_worker(args) -> ContinuousMomentumWorker:
    state_dir = Path(args.state_dir)
    output_dir = Path(args.output_dir)
    local = LocalCandidateSource(output_dir)
    source = local if args.candidate_source == "local" else FallbackCandidateSource(
        HttpCandidateSource(args.scan_data_base_url), local
    )
    sinks = [FileAlertSink(output_dir / "v4_alerts.ndjson")]
    telegram = TelegramAlertSink.from_environment()
    if telegram is not None:
        sinks.append(telegram)
    return ContinuousMomentumWorker(
        source=source,
        adapter=YahooPollingAdapter(max_workers=args.max_workers),
        engine=MomentumEngine(FileEventStore(state_dir)),
        alerts=AlertRouter(state_dir / "v4_alert_dispatch.json", sinks),
        health=HealthRecorder(
            output_dir / "v4_worker_cycles.csv",
            output_dir / "v4_worker_health.json",
        ),
        output_dir=output_dir,
        settings=WorkerSettings(
            hot_limit=args.hot_limit,
            warm_limit=args.warm_limit,
            warm_batch_size=args.warm_batch_size,
            warm_every_cycles=args.warm_every_cycles,
            market_interval_seconds=args.market_interval,
            off_hours_interval_seconds=args.off_hours_interval,
            max_source_age_seconds=args.max_source_age,
            min_provider_coverage=args.min_provider_coverage,
            failure_backoff_initial_seconds=args.failure_backoff_initial,
            failure_backoff_max_seconds=args.failure_backoff_max,
        ),
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Market Hunt V4.1 continuous shadow worker")
    value.add_argument("--once", action="store_true")
    value.add_argument("--max-cycles", type=int, default=None)
    value.add_argument("--candidate-source", choices=["remote", "local"], default="remote")
    value.add_argument(
        "--scan-data-base-url",
        default="https://raw.githubusercontent.com/AmolBhosale1989/ai-market-scanner/scan-data/dashboard-data",
    )
    value.add_argument("--state-dir", default=".state/v4")
    value.add_argument("--output-dir", default=str(OUTPUT_DIR))
    value.add_argument("--hot-limit", type=int, default=20)
    value.add_argument("--warm-limit", type=int, default=80)
    value.add_argument("--warm-batch-size", type=int, default=10)
    value.add_argument("--warm-every-cycles", type=int, default=5)
    value.add_argument("--market-interval", type=int, default=60)
    value.add_argument("--off-hours-interval", type=int, default=900)
    value.add_argument("--max-workers", type=int, default=8)
    value.add_argument("--max-source-age", type=int, default=43_200)
    value.add_argument("--min-provider-coverage", type=float, default=0.80)
    value.add_argument("--failure-backoff-initial", type=int, default=300)
    value.add_argument("--failure-backoff-max", type=int, default=1800)
    return value


if __name__ == "__main__":
    args = parser().parse_args()
    worker = build_worker(args)
    if args.once:
        metric = worker.run_cycle()
        print(metric)
        raise SystemExit(0 if metric.success else 1)
    worker.run_forever(max_cycles=args.max_cycles)
