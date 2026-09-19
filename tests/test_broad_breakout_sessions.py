from datetime import date
import pandas as pd
from scanner.broad_breakout import _latest_session_date

def test_weekend_uses_latest_warehouse_session():
    idx=pd.DatetimeIndex(["2026-09-17T15:00:00Z","2026-09-18T19:55:00Z"]).tz_convert("America/New_York")
    frame=pd.DataFrame({"Close":[100,101]},index=idx)
    assert _latest_session_date(frame,date(2026,9,19)) == date(2026,9,18)

def test_holiday_uses_latest_warehouse_session():
    idx=pd.DatetimeIndex(["2026-09-04T19:55:00Z"]).tz_convert("America/New_York")
    frame=pd.DataFrame({"Close":[100]},index=idx)
    assert _latest_session_date(frame,date(2026,9,7)) == date(2026,9,4)

def test_empty_frame_uses_fallback():
    fallback=date(2026,9,19)
    assert _latest_session_date(pd.DataFrame(),fallback) == fallback
