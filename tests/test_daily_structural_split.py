import pandas as pd
import pytest

from scanner import main, control_plane as cp, universe_plan


def catalogue():
    return pd.DataFrame([
        {'ticker':'AAA','name':'Alpha','exchange':'NASDAQ'},
        {'ticker':'BBB','name':'Beta','exchange':'NASDAQ'},
    ])


def liquidity(tickers):
    return pd.DataFrame([dict(ticker=t, tradable=True, price=50., avg_dollar_volume20=1e8,
                             avg_share_volume20=1e7, median_dollar_volume20=1e8,
                             adr20_pct=5., max_up_day_30d_pct=12.,ret20_pct=10.) for t in tickers])


def test_prepare_only_stops_before_all_enrichment_and_scoring(monkeypatch, memory_control_plane):
    monkeypatch.delenv('WAREHOUSE_CONSUMER_SNAPSHOT',raising=False)
    monkeypatch.setattr(main,'load_or_build_universe',lambda **k:catalogue())
    monkeypatch.setattr(main,'warehouse_frames',lambda tickers,**k:{t:pd.DataFrame() for t in tickers})
    monkeypatch.setattr(main,'build_tradable_rows',lambda frames:liquidity(frames))
    def forbidden(*a,**k):
        pytest.fail('Preparation crossed into anchor-bound evaluation')
    for name in ('consumer_anchor','consumer_pg_snapshot','rank_themes','build_event_watchlist',
                 '_benchmark_context','analyze_dataframe','enrich_earnings_intelligence',
                 'enrich_candidate_themes','enrich_candidates','_final_decision','finalize_daily'):
        monkeypatch.setattr(main,name,forbidden)
    out=main.run(prepare_only=True)
    assert set(out.ticker)=={'AAA','BBB'}
    assert set(cp.read_dataset('daily_structural_universe').columns)=={'ticker'}
    names={name for _,name in memory_control_plane['datasets']}
    assert names=={'tradable_universe','daily_structural_universe','live_universe'}
    assert not {'final_score','final_decision','catalyst_score','stage'} & set(out.columns)


def test_plan_preserves_entire_structural_universe_beyond_rank_limit():
    source=liquidity(['AAA','BBB'])
    plan=universe_plan.build_live_plan(source,limit=1,required=source[['ticker']])
    assert set(plan.ticker)=={'AAA','BBB'}
    assert plan.avg_dollar_volume20.notna().all()


def test_plan_rejects_unknown_required_ticker():
    with pytest.raises(RuntimeError,match='OUTSIDE_TRADABLE'):
        universe_plan.build_live_plan(liquidity(['AAA']),required=pd.DataFrame({'ticker':['OTHER']}))


@pytest.mark.parametrize('anchor,snapshot', [(None,None),(pd.Timestamp('2026-09-25T20:00Z'),None)])
def test_finalize_rejects_missing_boundary_before_any_io(monkeypatch,anchor,snapshot):
    monkeypatch.setattr(main,'consumer_anchor',lambda:anchor)
    monkeypatch.setattr(main,'consumer_pg_snapshot',lambda:snapshot)
    monkeypatch.setattr(main,'read_dataset',lambda *a:pytest.fail('read before boundary validation'))
    with pytest.raises(RuntimeError,match='DAILY_FINALIZATION_ANCHOR_REQUIRED'):
        main.run(snapshot_finalize=True)


def test_finalize_rebuilds_technical_inputs_then_enriches_at_t0(monkeypatch,memory_control_plane):
    anchor=pd.Timestamp('2026-09-25T20:00Z')
    monkeypatch.setattr(main,'consumer_anchor',lambda:anchor)
    monkeypatch.setattr(main,'consumer_pg_snapshot',lambda:'100:100:')
    cp.write_dataset('master_universe',catalogue())
    # The plan is structural. Poisoned old scores cannot be reused.
    cp.write_dataset('live_universe',pd.DataFrame([{'ticker':'AAA','final_score':-999}]))
    cp.write_dataset('tradable_universe',liquidity(['AAA','BBB']))
    cp.write_dataset('daily_prepared_candidates',pd.DataFrame([{'ticker':'OLD','final_score':-999}]))
    frozen=cp.read_dataset('tradable_universe')
    calls=[]
    def histories(tickers,**kwargs):
        assert list(tickers)==['AAA']  # Never evaluate unplanned BBB.
        calls.append('warehouse')
        return {'AAA':pd.DataFrame({'close':[60.]*230})}
    monkeypatch.setattr(main,'warehouse_frames',histories)
    monkeypatch.setattr(main,'build_tradable_rows',lambda frames:liquidity(frames))
    monkeypatch.setattr(main,'rank_themes',lambda **k:pd.DataFrame())
    monkeypatch.setattr(main,'build_event_watchlist',lambda f:pd.DataFrame())
    monkeypatch.setattr(main,'_benchmark_context',lambda:(0,{'regime_state':'STRONG','regime_score':90}))
    def analyze(ticker,hist,**kwargs):
        calls.append('technical')
        return dict(ticker=ticker,price=hist.close.iloc[-1],avg_dollar_volume=1e8,atr_pct=3.,
                    stage='CONFIRMED',technical_score=80.,formation_score=70.,rs20_vs_spy=5.,
                    risk_score=10.,entry_model='BREAKOUT',pattern='',decision='BUY / CONFIRMED')
    monkeypatch.setattr(main,'analyze_dataframe',analyze)
    monkeypatch.setattr(main,'merge_technical_context',lambda e,d:e)
    monkeypatch.setattr(main,'enrich_earnings_intelligence',lambda e:e)
    monkeypatch.setattr(main,'enrich_candidate_themes',lambda df,*a,**k:df.assign(
        theme_state='STRONG',theme_match_confidence=1.,theme_bonus=5.))
    def enrich(df,**kwargs):
        calls.append('catalyst')
        assert main.consumer_anchor()==anchor
        assert main.consumer_pg_snapshot()=='100:100:'
        assert df.price.tolist()==[60.]
        assert 'final_score' not in df
        return df.assign(catalyst_score=60.,negative_catalyst_risk=False,catalyst_gate_ok=True)
    monkeypatch.setattr(main,'enrich_candidates',enrich)
    def finish(df,pf,health,top_n,**kwargs):
        calls.append('finalize')
        assert kwargs['defer_publication'] is True
        assert df.final_score.iloc[0] != -999
        pd.testing.assert_frame_equal(cp.read_dataset('daily_prepared_candidates'),df)
        return df
    monkeypatch.setattr(main,'finalize_daily',finish)
    result=main.run(snapshot_finalize=True,defer_publication=True)
    assert result.ticker.tolist()==['AAA']
    assert calls.index('technical') < calls.index('catalyst') < calls.index('finalize')
    pd.testing.assert_frame_equal(cp.read_dataset('tradable_universe'),frozen)
    assert cp.read_dataset('live_universe').final_score.tolist()==[-999]


def test_daily_theme_ranking_cannot_overwrite_live_theme_product(monkeypatch):
    from scanner import themes
    existing=pd.DataFrame([{'theme':'TEST','live_theme_score':88.}])
    cp.write_dataset('trending_themes',existing)
    bars=pd.DataFrame({'Close':range(100,180),'EMA20':150.,'EMA50':140.,'EMA200':130.})
    monkeypatch.setattr(themes,'THEMES',{'TEST':{'etf':'TEST'}})
    monkeypatch.setattr(themes,'warehouse_frames',lambda *a,**k:{'SPY':bars,'TEST':bars})
    monkeypatch.setattr(themes,'add_indicators',lambda df:df)
    assert not themes.rank_themes(persist=False).empty
    pd.testing.assert_frame_equal(cp.read_dataset('trending_themes'),existing)
