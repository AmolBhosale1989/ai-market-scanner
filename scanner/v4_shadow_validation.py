from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from .config import OUTPUT_DIR
from .data import download_batch
from .v4.shadow_validation import ShadowValidationLedger, infer_as_of_session
from .v4.source import HttpCandidateSource


def _csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return pd.DataFrame()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_ledger(state_dir: Path, output_dir: Path) -> ShadowValidationLedger:
    return ShadowValidationLedger(
        state_file=state_dir / "v4_shadow_observations.json",
        observations_csv=output_dir / "v4_shadow_observations.csv",
        summary_csv=output_dir / "v4_shadow_strategy_summary.csv",
        daily_csv=output_dir / "v4_shadow_daily_comparison.csv",
        breakdowns_csv=output_dir / "v4_shadow_breakdowns.csv",
        health_json=output_dir / "v4_shadow_validation_health.json",
    )


def run(state_dir: Path, output_dir: Path, as_of_session: str = "") -> pd.DataFrame:
    ledger = build_ledger(state_dir, output_dir)
    unresolved = ledger.unresolved_tickers()
    histories = download_batch(unresolved, period="3mo", interval="1d") if unresolved else {}
    ledger.resolve_histories(histories)

    source = HttpCandidateSource().load()
    v3 = source.frame
    v45 = _csv(output_dir / "v4_5_ranked_candidates.csv")
    model = _json(state_dir / "v4_5_model.json") or _json(output_dir / "v4_5_model.json")
    session = as_of_session or infer_as_of_session(v3, source.source_timestamp_utc)
    frame = ledger.record_snapshot(
        v3,
        v45,
        as_of_session=session,
        observed_at_utc=source.source_timestamp_utc,
        model_payload=model,
        source_name=source.source_name,
    )
    print(
        f"V4 shadow validation: session={session} observations={len(frame)} "
        f"mature={(pd.to_numeric(frame.get('daily_bars_resolved'), errors='coerce').fillna(0) >= 5).sum() if len(frame) else 0}"
    )
    return frame


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Hunt V3 versus V4.5 daily shadow validation")
    parser.add_argument("--state-dir", default=".state/v4")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--as-of-session", default="")
    args = parser.parse_args()
    run(Path(args.state_dir), Path(args.output_dir), args.as_of_session)
