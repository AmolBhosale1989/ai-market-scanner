import pandas as pd
import pytest
from scanner.signal_freshness import signal_expiry_reason, SIGNAL_BAR_FIELDS

def rows(stamp):
    return {name: [{'ticker':'TEST', field:stamp}] for name,field in SIGNAL_BAR_FIELDS.items()}

def test_old_signal_cannot_hide_behind_fresh_snapshot():
    assert signal_expiry_reason(rows('2026-09-25T14:50:00-04:00'), '2026-09-25T19:09:33Z')
    assert not signal_expiry_reason(rows('2026-09-25T14:55:00-04:00'), '2026-09-25T19:09:33Z')

@pytest.mark.parametrize('stamp',[None,'bad','2026-09-25T18:55:00','2026-09-25T19:10:00Z','2026-09-24T18:55:00Z'])
def test_invalid_missing_future_prior_session_blocks(stamp):
    assert signal_expiry_reason(rows(stamp), '2026-09-25T19:09:33Z')

def test_exact_limit_and_read_time_expiry():
    data=rows('2026-09-25T18:55:00Z')
    assert not signal_expiry_reason(data, '2026-09-25T19:10:00Z')
    assert signal_expiry_reason(data, '2026-09-25T19:10:00.001Z')

def test_empty_signals_valid_but_missing_dataset_not_valid():
    assert not signal_expiry_reason({name:[] for name in SIGNAL_BAR_FIELDS},'2026-09-25T19:00Z')
    assert signal_expiry_reason({},'2026-09-25T19:00Z')

def test_market_closed_uses_latest_session_close():
    assert not signal_expiry_reason(rows('2026-09-25T19:55:00Z'),'2026-09-26T05:00Z')


@pytest.mark.parametrize('name', list(SIGNAL_BAR_FIELDS))
def test_one_expired_family_blocks_otherwise_fresh_publication(name):
    data = rows('2026-09-25T18:55:00Z')
    data[name][0][SIGNAL_BAR_FIELDS[name]] = '2026-09-25T18:50:00Z'
    assert name in signal_expiry_reason(data, '2026-09-25T19:09:33Z')


def test_dashboard_passes_canonical_names_for_every_checked_family():
    import ast
    from pathlib import Path
    tree = ast.parse(Path('app.py').read_text())
    files = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                 if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'files' for t in node.targets))
    assert set(SIGNAL_BAR_FIELDS) <= set(files.values())
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == 'signal_expiry_reason')
    # The dashboard aliases (live/themes) must not look like absent datasets.
    assert isinstance(call.args[0], ast.DictComp)
