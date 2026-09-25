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
