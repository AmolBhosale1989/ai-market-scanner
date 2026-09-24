import pandas as pd
import pytest

from scanner import intraday


@pytest.mark.parametrize("prior,new", [([], []), ([{"ticker": "SPY"}], []),
                                     ([{"ticker": "SPY"}], [{"ticker": "QQQ"}])])
def test_transition_version_preserves_history_even_without_changes(monkeypatch, prior, new):
    written = {}
    states = []
    monkeypatch.setattr(intraday, "read_state", lambda *a, **k: prior)
    monkeypatch.setattr(intraday, "append_state", lambda *a: states.append(a))
    monkeypatch.setattr(intraday, "write_dataset", lambda n, f, **k: written.update({n: f}))
    intraday._write_state_transitions(new)
    assert written["state_transitions"].to_dict("records") == prior + new
    assert bool(states) == bool(new)


def test_no_candidates_materializes_transition_and_journal_versions(monkeypatch):
    written = {}
    monkeypatch.setattr(intraday, "read_state", lambda *a, **k: [])
    monkeypatch.setattr(intraday, "append_state", lambda *a, **k: None)
    monkeypatch.setattr(intraday, "write_dataset", lambda n, f, **k: written.update({n: f}))
    for name in ("build_performance_reports", "build_empirical_calibration",
                 "_write_monitor_health", "_write_recommendations", "build_product_feed"):
        monkeypatch.setattr(intraday, name, lambda *a, **k: None)
    intraday.run(input_frame=pd.DataFrame(columns=["stage"]))
    assert {"state_transitions", "paper_journal", "intraday_live"} <= written.keys()
    assert all(frame.empty for frame in written.values())


def test_corrupt_transition_history_still_blocks(monkeypatch):
    def corrupt(*a, **k):
        raise RuntimeError("CONTROL_PLANE_STATE_CORRUPT")
    monkeypatch.setattr(intraday, "read_state", corrupt)
    with pytest.raises(RuntimeError, match="CONTROL_PLANE_STATE_CORRUPT"):
        intraday._write_state_transitions([])
