from __future__ import annotations

from datetime import timedelta

from .consumer_snapshot import consumer_anchor
from .control_plane import read_dataset


REQUIRED_PROVIDERS=("YAHOO_NEWS","SEC_EDGAR","ALPHA_VANTAGE")
PROVIDER_MAX_AGE={"YAHOO_NEWS":timedelta(minutes=15),"SEC_EDGAR":timedelta(minutes=15),
                  "ALPHA_VANTAGE":timedelta(hours=24)}


def run() -> dict:
    """Live control-plane gate: database-only, no provider/network access."""
    universe=read_dataset("live_universe")
    if universe.empty or "ticker" not in universe.columns:
        raise RuntimeError("CATALYST_LIVE_UNIVERSE_EMPTY")
    tickers=list(dict.fromkeys(universe["ticker"].dropna().astype(str).str.upper()))
    anchor=consumer_anchor()
    if anchor is None:
        raise RuntimeError("CATALYST_CONTEXT_ANCHOR_REQUIRED")
    verify_coverage(tickers,anchor=anchor)
    result={"tickers":len(tickers),"providers":len(REQUIRED_PROVIDERS),
            "required_checks":len(tickers)*len(REQUIRED_PROVIDERS)}
    print("CATALYST_COVERAGE_PASS "+str(result),flush=True)
    return result


def verify_coverage(tickers,*,anchor):
    from .database import connection
    wanted=list(dict.fromkeys(str(x).upper() for x in tickers if x))
    with connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT ticker,provider,max(checked_at)
                       FROM catalyst_check
                       WHERE ticker=ANY(%s) AND checked_at<=%s
                         AND result_status IN ('EVENTS','NO_EVENT')
                         AND rejected_count=0
                       GROUP BY ticker,provider""",(wanted,anchor))
        rows=cur.fetchall()
        present={(ticker,provider) for ticker,provider,checked_at in rows
                 if anchor-checked_at <= PROVIDER_MAX_AGE[provider]}
    required={(ticker,provider) for ticker in wanted for provider in REQUIRED_PROVIDERS}
    missing=required-present
    if missing:
        sample=",".join(f"{t}:{p}" for t,p in sorted(missing)[:10])
        raise RuntimeError(f"CATALYST_COVERAGE_INCOMPLETE missing={len(missing)} sample={sample}")
    return True


def main():
    return run()


if __name__=="__main__":
    main()
