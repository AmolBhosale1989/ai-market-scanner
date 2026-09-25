"""Choose a complete dependency graph before seeding any daily artifacts."""
import argparse
import pandas as pd
from .market_cutoff import completed_daily_session, daily_clock
from .control_plane import publication_info


def select_mode(requested, manifest, *, now=None):
    if requested not in {"full", "live"}:
        raise ValueError("Unknown pipeline mode")
    if requested == "full":
        return "full"
    now = pd.Timestamp(now if now is not None else daily_clock())
    if not manifest or manifest.get("status") != "PUBLISHED":
        return "full"
    observed = pd.Timestamp(manifest.get("warehouse_as_of_utc"))
    if pd.isna(observed) or observed.tzinfo is None or observed > now:
        raise RuntimeError("RUN_POLICY_INVALID_PUBLICATION_TIME")
    # A live run may reuse daily artifacts only for the same completed session.
    # At the closing bell the dependency graph must rebuild daily data first.
    return "live" if completed_daily_session(observed) == completed_daily_session(now) else "full"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requested", choices=["full", "live"], required=True)
    args = parser.parse_args()
    manifest = None
    if args.requested == "live":
        try:
            manifest = publication_info("production")
        except RuntimeError as exc:
            if "CONTROL_PLANE_PUBLICATION_UNAVAILABLE" not in str(exc):
                raise
    print(select_mode(args.requested, manifest))


if __name__ == "__main__":
    main()
