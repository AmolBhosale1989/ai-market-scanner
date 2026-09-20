import pandas as pd

from scanner.universe_plan import select_live_universe


def test_live_universe_is_deterministic_and_shared():
    frame=pd.DataFrame({
        "ticker":["C","A","B"],"avg_dollar_volume20":[10,30,20],
        "adr20_pct":[1,3,2],"max_up_day_30d_pct":[5,12,8],"ret20_pct":[1,3,2],
    })
    first=select_live_universe(frame,limit=2)
    second=select_live_universe(frame.sample(frac=1,random_state=7),limit=2)
    assert first["ticker"].tolist()==second["ticker"].tolist()==["A","B"]
