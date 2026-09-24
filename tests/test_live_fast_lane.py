from pathlib import Path
import subprocess
import textwrap

import pandas as pd

from scanner import v3_live


def test_v3_can_reuse_current_run_broad_discovery(monkeypatch):
    current=pd.DataFrame([{"ticker":"AAA","broad_breakout_score":88.0}])
    monkeypatch.setattr(v3_live,"read_dataset",lambda name: current.copy())
    monkeypatch.setattr(
        v3_live,
        "run_broad_discovery",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("broad discovery reran")),
    )

    result=v3_live._fresh_discovery(reuse_current_broad_discovery=True)

    assert result["ticker"].tolist()==["AAA"]
    assert result["v3_discovery_source"].eq("POSTGRES_WAREHOUSE_DISCOVERY").all()


def test_v3_rejects_empty_reused_discovery(monkeypatch):
    monkeypatch.setattr(v3_live,"read_dataset",lambda name:pd.DataFrame())

    try:
        v3_live._fresh_discovery(reuse_current_broad_discovery=True)
    except RuntimeError as exc:
        assert "no qualified candidates" in str(exc)
    else:
        raise AssertionError("empty current-run discovery did not fail closed")


def test_market_open_lane_is_bounded_and_preserves_final_gates():
    workflow=Path(".github/workflows/production.yml").read_text()
    live=workflow.split('          else\n            # The market-open lane',1)[1].split('          fi\n\n          stage audit',1)[0]

    assert "& broad_pid=$!" in live
    assert "& theme_pid=$!" in live
    assert "& sector_pid=$!" in live
    assert "& premarket_pid=$!" in live
    assert "& v4_pid=$!" in live
    assert "--reuse-current-broad-discovery" in live
    assert live.index('wait_stage "$broad_pid"') < live.index("-m scanner.v3_live")
    assert live.index('wait_stage "$sector_pid"') < live.index("-m scanner.momentum_signals")
    assert live.index("-m scanner.momentum_signals") < live.index("-m scanner.order_flow_strategy")
    assert live.index('wait_stage "$v3_pid"') < live.index("-m scanner.daily_pick")
    assert "scanner.quant_shadow" not in live
    assert "scanner.social_engine" not in live
    assert "scanner.v8_operations" not in live
    assert "scanner.system_health --require-production" in live
    assert "stage audit 280" in workflow
    assert "stage acceptance 290" in workflow
    assert "production_telemetry --finalize --mode production" in workflow


def _execute_live_workflow(fail_at=""):
    workflow=Path(".github/workflows/production.yml").read_text()
    body=textwrap.dedent(workflow.split("      - name: Execute complete dependency chain",1)[1]
                         .split("        run: |\n",1)[1])
    shim='''
PIPELINE_MODE=live
python() {
  printf '%s\\n' "$*"
  if [[ "$FAIL_AT" == premarket && "$*" == *scanner.premarket* ]]; then return 7; fi
  return 0
}
timeout() { shift; "$@"; }
bash() { printf '%s\\n' "$*"; }
'''
    return subprocess.run(
        ["bash"],input=f"FAIL_AT={fail_at}\n"+shim+body,text=True,capture_output=True,
    )


def test_market_open_lane_shell_graph_reaches_atomic_publication():
    result=_execute_live_workflow()
    assert result.returncode==0,result.stderr
    assert "scanner.premarket" in result.stdout
    assert "scanner.v3_live --reuse-current-broad-discovery" in result.stdout
    assert "scanner.production_acceptance" in result.stdout
    assert "scanner.production_telemetry --finalize --mode production" in result.stdout


def test_parallel_failure_blocks_dependents_and_publication():
    result=_execute_live_workflow("premarket")
    assert result.returncode!=0
    assert "scanner.premarket" in result.stdout
    assert "scanner.production_acceptance" not in result.stdout
    assert "scanner.production_telemetry --finalize --mode production" not in result.stdout
