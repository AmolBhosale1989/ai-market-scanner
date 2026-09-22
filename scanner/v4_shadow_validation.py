from __future__ import annotations

import argparse

import pandas as pd

from .control_plane import read_dataset, read_state
from .v4.shadow_validation import ShadowValidationLedger, infer_as_of_session
from .v4.source import ControlPlaneCandidateSource
from .warehouse import frames as warehouse_frames


def build_ledger() -> ShadowValidationLedger:
    return ShadowValidationLedger()


def resolve_existing() -> pd.DataFrame:
    ledger = build_ledger()
    unresolved = ledger.unresolved_tickers()
    histories = warehouse_frames(unresolved, period="3mo", interval="1d", require_complete=False) if unresolved else {}
    return ledger.resolve_histories(histories)


def record_current(as_of_session: str = "") -> pd.DataFrame:
    ledger = build_ledger()
    source = ControlPlaneCandidateSource().load()
    model = read_state("v4_models", "v4_5_model", default={}) or {}
    session = as_of_session or infer_as_of_session(source.frame, source.source_timestamp_utc)
    frame = ledger.record_snapshot(
        source.frame,
        read_dataset("v4_5_ranked_candidates", required=False),
        as_of_session=session,
        observed_at_utc=source.source_timestamp_utc,
        model_payload=model,
        source_name=source.source_name,
        v5_candidates=read_dataset("v5_ranked_candidates", required=False),
        v6_candidates=read_dataset("v6_ranked_candidates", required=False),
        v7_candidates=read_dataset("v7_paper_portfolio", required=False),
    )
    print(f"V4 shadow validation: session={session} observations={len(frame)}")
    return frame


def run(as_of_session: str = "") -> pd.DataFrame:
    resolve_existing()
    return record_current(as_of_session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V3 versus V4.5 daily shadow validation")
    parser.add_argument("--as-of-session", default="")
    parser.add_argument("--resolve-only", action="store_true")
    parser.add_argument("--record-only", action="store_true")
    args = parser.parse_args()
    if args.resolve_only and args.record_only:
        parser.error("choose only one of --resolve-only or --record-only")
    if args.resolve_only:
        resolve_existing()
    elif args.record_only:
        record_current(args.as_of_session)
    else:
        run(args.as_of_session)
