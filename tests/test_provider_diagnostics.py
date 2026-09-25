import pandas as pd
from scanner.provider_diagnostics import describe_response


def test_diagnostics_preserve_missing_buckets_and_zero_volume():
    frame = pd.DataFrame({'Volume':[100,0,10]}, index=pd.to_datetime([
        '2026-09-25T16:50Z','2026-09-25T17:00Z','2026-09-25T17:10Z']))
    before=frame.copy(deep=True)
    now=pd.Timestamp('2026-09-25T17:14:56Z')
    result=describe_response('SKYY',frame,period='2d',interval='5m',started=now-pd.Timedelta(seconds=2),received=now,elapsed=2)
    assert result['age_at_response_seconds']==596
    assert result['latest_bar_volume']==0
    assert result['completed_rows']==2
    assert result['unreturned_internal_buckets']==['2026-09-25T16:55:00+00:00']
    assert result['provider_reported_time'] is None
    assert result['response_headers_available'] is False
    pd.testing.assert_frame_equal(frame,before)


def test_missing_response_does_not_invent_zero_volume():
    now=pd.Timestamp('2026-09-25T17:14:56Z')
    result=describe_response('SKYY',None,period='2d',interval='5m',started=now,received=now,elapsed=0)
    assert result['returned_rows']==0
    assert 'is_zero_volume' not in result
