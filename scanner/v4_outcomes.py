from __future__ import annotations

import argparse

from .control_plane import append_state, write_dataset, write_record
from .v4.calibration import fit_model
from .v4.evidence import training_outcomes
from .v4.outcomes import SignalOutcomeLedger
from .v4.source import ControlPlaneCandidateSource
from .warehouse import frames as warehouse_frames


def build_ledger() -> SignalOutcomeLedger:
    return SignalOutcomeLedger()


def resolve_daily():
    ledger = build_ledger()
    records = ledger.load_records()
    tickers = sorted({str(record.get("ticker", "")) for record in records.values()
                      if record.get("entered_at_utc")})
    histories = warehouse_frames(tickers, period="3mo", interval="1d", require_complete=False)
    frame = ledger.resolve_daily_histories(histories)
    print(f"V4.2 daily outcomes resolved: signals={len(frame)}, tickers={len(tickers)}")
    return frame


def fit_calibration():
    model, validation = fit_model(training_outcomes())
    append_state("v4_models", "v4_5_model", model.payload)
    write_record("v4_5_model", model.payload)
    write_dataset("v4_5_validation", validation, entity_key=None)
    candidates = ControlPlaneCandidateSource().load().frame
    scored = model.score(candidates) if model.payload.get("targets") else candidates.iloc[0:0].copy()
    write_dataset("v4_5_ranked_candidates", scored, entity_key="ticker")
    print(f"V4.5 calibration: status={model.promotion_status} version={model.version} samples={model.payload.get('samples', 0)}")
    return model, validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V4 outcome resolver and calibration")
    parser.add_argument("--resolve-daily", action="store_true")
    parser.add_argument("--fit-calibration", action="store_true")
    args = parser.parse_args()
    if args.resolve_daily:
        resolve_daily()
    if args.fit_calibration:
        fit_calibration()
