import sys
import subprocess
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from scanner import live, main, universe_plan, warehouse


def candidates():
    return pd.DataFrame([
        {"ticker":"TDS","stage":"ARMED","final_score":90},
        {"ticker":"OTHER","stage":"FORMING","final_score":80},
        {"ticker":"REJECTED","stage":"REJECT","final_score":100},
    ])


def test_planned_candidates_match_live_consumers(monkeypatch):
    consumed=[]
    monkeypatch.setattr(live,"warehouse_frames",lambda tickers,**kwargs: {ticker:pd.DataFrame() for ticker in tickers})
    monkeypatch.setattr(live,"analyze_live_candidate",
                        lambda **kwargs: consumed.append(kwargs["ticker"]) or {})
    selected=live.select_live_candidates(candidates(),limit=1)
    live.enrich_live_candidates(candidates(),limit=1)
    assert selected["ticker"].tolist()==consumed==["TDS"]


def test_plan_includes_confirmation_candidate_outside_ranked_limit(monkeypatch,memory_control_plane):
    from scanner.control_plane import write_dataset, read_dataset
    write_dataset("tradable_universe",pd.DataFrame([
        {"ticker":"LIQUID","avg_dollar_volume20":1e9},
        {"ticker":"TDS","avg_dollar_volume20":1e7},
    ]))
    write_dataset("daily_prepared_candidates",candidates())
    monkeypatch.setattr(sys,"argv",["universe_plan","--limit","1","--include-daily-candidates"])
    universe_plan.main()
    assert set(read_dataset("live_universe")["ticker"])=={"LIQUID","TDS","OTHER"}


@pytest.mark.parametrize("failure",["missing","stale","invalid","short"])
def test_invalid_intraday_blocks_confirmation_and_outputs(monkeypatch,memory_control_plane,failure):
    now=pd.Timestamp.now(tz="UTC")
    frame=pd.DataFrame([{
        "ticker":"TDS","event_timestamp":now-pd.Timedelta(days=30,minutes=i*5),
        "ingested_at":now,"open":10.,"high":11.,"low":9.,"close":10.,"volume":100.,
    } for i in range(20)])
    if failure=="missing":
        frame["ticker"]="WRONG"
    elif failure=="invalid":
        frame.loc[0,"close"]=-1
    elif failure=="short":
        frame=frame.head(1)
    monkeypatch.setattr(warehouse,"_pit",lambda *a,**k:frame)
    monkeypatch.setattr(main,"enrich_live_candidates",
                        lambda *a,**k:pytest.fail("confirmation ran before validation"))
    with pytest.raises(RuntimeError,match="WAREHOUSE_"):
        main.finalize_daily(candidates().head(1),pd.DataFrame(),{})
    assert memory_control_plane["datasets"]=={}


def test_validation_precedes_confirmation(monkeypatch):
    calls=[]
    def validate(req):
        assert req.tickers==("TDS",)
        assert req.max_age_minutes==10
        assert req.minimum_fresh_coverage==1.0
        calls.append("gate")
    def confirm(*a,**k):
        calls.append("confirmation")
        raise RuntimeError("TEST_STOP_AFTER_CONFIRMATION")
    monkeypatch.setattr(main,"provide",validate)
    monkeypatch.setattr(main,"enrich_live_candidates",confirm)
    with pytest.raises(RuntimeError,match="TEST_STOP_AFTER_CONFIRMATION"):
        main.finalize_daily(candidates().head(1),pd.DataFrame(),{})
    assert calls==["gate","confirmation"]


@pytest.mark.parametrize("fail_at",["critical_refresh","critical_gate","refresh","gate",""])
def test_full_workflow_stops_before_consumers_on_intraday_failure(fail_at):
    workflow=Path(".github/workflows/production.yml").read_text()
    body=textwrap.dedent(workflow.split("      - name: Execute complete dependency chain",1)[1]
                         .split("        run: |\n",1)[1]
                         .split("\n      - name:",1)[0])
    shim='''
GITHUB_ENV=/dev/null
PIPELINE_MODE=full
python() {
  if [[ "$*" == *scanner.run_policy* ]]; then echo full; return 0; fi
  printf '%s\\n' "$*"
  if [[ "$FAIL_AT" == critical_refresh && "$*" == *scanner.warehouse_refresh* && "$*" == *--critical-only* ]]; then return 124; fi
  if [[ "$FAIL_AT" == critical_gate && "$*" == *scanner.warehouse_gate* && "$*" != *MASTER_DAILY* ]]; then return 9; fi
  if [[ "$FAIL_AT" == refresh && "$*" == *scanner.live_ingestion* ]]; then return 7; fi
  if [[ "$FAIL_AT" == gate && "$*" == *scanner.warehouse_gate* && "$*" == *LIVE_INTRADAY* ]]; then return 8; fi
  return 0
}
timeout() { shift; "$@"; }
bash() { printf '%s\\n' "$*"; }
'''
    result=subprocess.run(["bash"],input=f"FAIL_AT={fail_at}\n"+shim+body,
                          text=True,capture_output=True)
    if fail_at:
        assert result.returncode!=0
        if fail_at.startswith("critical_"):
            assert "--dataset master_universe" not in result.stdout
            assert "--prepare-only" not in result.stdout
        assert "--finalize-prepared" not in result.stdout
        assert "-m scanner.v3_live" not in result.stdout
        assert "--finalize --mode production" not in result.stdout
    else:
        assert result.returncode==0,result.stderr
        trace=result.stdout
        assert trace.index("--prepare-only") < trace.index("-m scanner.live_ingestion")
        assert trace.index("--tier LIVE_INTRADAY") < trace.index("--finalize-prepared")
        assert trace.index("--finalize-prepared") < trace.index("-m scanner.v3_live")
