import numpy as np
import pandas as pd
import pytest
from scanner.daily_repair import repair_daily


def inputs():
    bars=pd.DataFrame({"Open":10.,"High":12.,"Low":9.,"Close":11.,"Volume":100.},
        index=pd.date_range("2026-09-22T13:30Z",periods=78,freq="5min"))
    daily=pd.DataFrame({"Open":[8.,np.nan],"High":[10.,np.nan],"Low":[7.,np.nan],
                        "Close":[9.,np.nan],"Volume":[500.,100.]},
                       index=pd.to_datetime(["2026-09-21","2026-09-22"]))
    return daily,bars


def test_complete_session_reconstruction_preserves_history_and_provenance():
    daily,bars=inputs()
    result=repair_daily(daily,bars,now="2026-09-23T06:00Z")
    assert result.iloc[-1].Close==11.
    assert result.iloc[-1].Volume==7800.
    assert result.iloc[-1].source_bar_count==78
    assert result.iloc[-1].daily_bar_source=="COMPLETE_REGULAR_SESSION_5M"
    pd.testing.assert_series_equal(result.iloc[0][daily.columns],daily.iloc[0],check_dtype=False)


@pytest.mark.parametrize("failure",["missing","duplicate","invalid","stale","open_session"])
def test_bad_or_incomplete_intraday_cannot_repair_daily(failure):
    daily,bars=inputs()
    now="2026-09-23T06:00Z"
    if failure=="missing": bars=bars.drop(bars.index[10])
    if failure=="duplicate": bars=pd.concat([bars,bars.iloc[-1:]])
    if failure=="invalid": bars.iloc[-1,bars.columns.get_loc("Close")]=np.inf
    if failure=="stale": bars.index=bars.index-pd.Timedelta(days=1)
    if failure=="open_session": now="2026-09-22T19:58Z"
    result=repair_daily(daily,bars,now=now)
    assert pd.isna(result.iloc[-1].Close)


def test_good_daily_prices_are_not_replaced():
    daily,bars=inputs(); daily.iloc[-1]=[10.,12.,9.,10.5,9000.]
    pd.testing.assert_frame_equal(repair_daily(daily,bars,now="2026-09-23T06:00Z"),daily)
