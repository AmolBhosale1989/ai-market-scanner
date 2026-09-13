from __future__ import annotations

import argparse

import pandas as pd
from pathlib import Path

from .config import OUTPUT_DIR
from .data import download_history
from .v4.calibration import fit_model, save_model
from .v4.outcomes import SignalOutcomeLedger
from .v4.replay import verify_deterministic_replay
from .v4.source import HttpCandidateSource


def build_ledger(state_dir: Path, output_dir: Path) -> SignalOutcomeLedger:
    return SignalOutcomeLedger(
        state_file=state_dir / "v4_outcomes.json",
        mirror_csv=output_dir / "v4_outcomes.csv",
        summary_csv=output_dir / "v4_outcome_summary.csv",
    )


def resolve_daily(state_dir: Path, output_dir: Path):
    ledger = build_ledger(state_dir, output_dir)
    records = ledger.load_records()
    tickers = sorted({
        str(record.get("ticker", ""))
        for record in records.values()
        if record.get("entered_at_utc")
    })
    histories = {ticker: download_history(ticker, "3mo", "1d") for ticker in tickers if ticker}
    frame = ledger.resolve_daily_histories(histories)
    print(f"V4.2 daily outcomes resolved: signals={len(frame)}, tickers={len(tickers)}")
    return frame


def fit_calibration(state_dir: Path, output_dir: Path):
    ledger = build_ledger(state_dir, output_dir)
    records = ledger.load_records()
    outcomes = pd.DataFrame(list(records.values()))
    model, validation = fit_model(outcomes)
    save_model(model, state_dir / "v4_5_model.json")
    output_dir.mkdir(parents=True, exist_ok=True)
    save_model(model, output_dir / "v4_5_model.json")
    validation.to_csv(output_dir / "v4_5_validation.csv", index=False)
    try:
        candidates = HttpCandidateSource().load().frame
        scored = model.score(candidates) if model.payload.get("targets") else candidates.copy()
        scored.to_csv(output_dir / "v4_5_ranked_candidates.csv", index=False)
    except Exception:
        pd.DataFrame().to_csv(output_dir / "v4_5_ranked_candidates.csv", index=False)
    print(
        f"V4.5 calibration: status={model.promotion_status} "
        f"version={model.version} samples={model.payload.get('samples', 0)}"
    )
    return model, validation


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V4.2 outcome resolver and replay check")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--resolve-daily", action="store_true")
    parser.add_argument("--verify-replay", action="store_true")
    parser.add_argument("--fit-calibration", action="store_true")
    args = parser.parse_args()
    state_dir, output_dir = Path(args.state_dir), Path(args.output_dir)
    if args.resolve_daily:
        resolve_daily(state_dir, output_dir)
    if args.fit_calibration:
        fit_calibration(state_dir, output_dir)
    if args.verify_replay:
        report = verify_deterministic_replay(state_dir / "v4_events.ndjson")
        print(report)
        if not report["deterministic"]:
            raise SystemExit(1)
