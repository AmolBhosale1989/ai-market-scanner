import pandas as pd
from scanner.main import _final_decision


def _row(**overrides):
    base={
      "decision":"BUY / CONFIRMED","stage":"CONFIRMED","catalyst_score":100,
      "negative_catalyst_risk":False,"catalyst_gate_ok":True,
    }
    base.update(overrides)
    return pd.Series(base)


def test_blind_catalyst_gate_dominates_positive_score():
    assert _final_decision(_row(catalyst_gate_ok=False,catalyst_score=100)) == "NO TRADE / CATALYST DATA BLIND"


def test_stale_catalyst_gate_dominates_positive_score():
    assert _final_decision(_row(catalyst_gate_ok=False,catalyst_status="STALE",catalyst_score=100)) == "NO TRADE / CATALYST DATA BLIND"


def test_available_gate_preserves_existing_decision_logic():
    assert _final_decision(_row(catalyst_gate_ok=True,catalyst_score=100)) == "BUY / CONFIRMED + CATALYST"
