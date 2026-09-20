from __future__ import annotations

from datetime import date

import pandas as pd

from scanner.session_contract import expected_market_data_session, latest_frame_session


def test_latest_frame_session_uses_friday_on_weekend_data():
    index=pd.DatetimeIndex([
        "2026-09-17T19:55:00Z",
        "2026-09-18T19:55:00Z",
    ])
    frame=pd.DataFrame({"Close":[10.0,11.0]},index=index)
    assert latest_frame_session(frame)==date(2026,9,18)


def test_expected_market_session_uses_friday_before_monday_open():
    assert expected_market_data_session(pd.Timestamp("2026-09-20T12:00:00Z"))==date(2026,9,18)
    assert expected_market_data_session(pd.Timestamp("2026-09-21T12:00:00Z"))==date(2026,9,18)


def test_expected_market_session_switches_to_monday_after_open():
    assert expected_market_data_session(pd.Timestamp("2026-09-21T14:00:00Z"))==date(2026,9,21)
