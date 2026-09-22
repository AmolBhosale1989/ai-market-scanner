from __future__ import annotations

import argparse

from .v4.adapters import YahooPollingAdapter
from .v4.alerting import AlertRouter, DatabaseAlertSink, TelegramAlertSink
from .v4.catalysts import CompositeCatalystAdapter, YahooNewsCatalystAdapter
from .v4.engine import MomentumEngine
from .v4.health import HealthRecorder
from .v4.options_microstructure import YahooOptionsMicrostructureAdapter
from .v4.outcomes import SignalOutcomeLedger
from .v4.source import ControlPlaneCandidateSource
from .v4.store import PostgresEventStore
from .v4.worker import ContinuousMomentumWorker, WorkerSettings


def build_worker(args) -> ContinuousMomentumWorker:
    sinks = [DatabaseAlertSink()]
    telegram = TelegramAlertSink.from_environment() if args.telegram_alerts else None
    if telegram is not None:
        sinks.append(telegram)
    catalysts = CompositeCatalystAdapter([
        YahooNewsCatalystAdapter(max_workers=args.catalyst_workers, max_tickers=args.news_limit)
    ]) if args.catalysts else None
    options = YahooOptionsMicrostructureAdapter(
        max_workers=args.options_workers,
        max_tickers=args.options_limit,
    ) if args.options_microstructure else None
    return ContinuousMomentumWorker(
        source=ControlPlaneCandidateSource(),
        adapter=YahooPollingAdapter(max_workers=args.max_workers),
        engine=MomentumEngine(PostgresEventStore()),
        alerts=AlertRouter(sinks),
        health=HealthRecorder(),
        outcome_ledger=SignalOutcomeLedger(),
        catalyst_adapter=catalysts,
        options_microstructure_adapter=options,
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
    value = argparse.ArgumentParser(description="Market Hunt V4 continuous shadow worker")
    value.add_argument("--once", action="store_true")
    value.add_argument("--max-cycles", type=int, default=None)
    value.add_argument("--hot-limit", type=int, default=20)
    value.add_argument("--warm-limit", type=int, default=80)
    value.add_argument("--warm-batch-size", type=int, default=10)
    value.add_argument("--warm-every-cycles", type=int, default=5)
    value.add_argument("--market-interval", type=int, default=60)
    value.add_argument("--off-hours-interval", type=int, default=900)
    value.add_argument("--max-workers", type=int, default=8)
    value.add_argument("--catalysts", action="store_true")
    value.add_argument("--catalyst-workers", type=int, default=4)
    value.add_argument("--news-limit", type=int, default=20)
    value.add_argument("--options-microstructure", action="store_true")
    value.add_argument("--options-workers", type=int, default=4)
    value.add_argument("--options-limit", type=int, default=8)
    value.add_argument("--telegram-alerts", action="store_true")
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
