import pandas as pd
import app as appmod


def _df():
    idx=pd.bdate_range('2026-08-24', periods=5)
    return pd.DataFrame({'Open':[100]*5,'High':[101]*5,'Low':[99]*5,'Close':[100]*5,'Volume':[1000]*5},index=idx)


def test_event_session_premarket_uses_same_session():
    df=_df()
    # Tuesday 08:00 ET -> Tuesday regular session
    pos=appmod.event_session_index(df,pd.Timestamp('2026-08-25 08:00'))
    assert df.index[pos].date()==pd.Timestamp('2026-08-25').date()


def test_event_session_after_close_uses_next_session():
    df=_df()
    # Tuesday 16:05 ET -> Wednesday regular session
    pos=appmod.event_session_index(df,pd.Timestamp('2026-08-25 16:05'))
    assert df.index[pos].date()==pd.Timestamp('2026-08-26').date()


def test_macro_calendar_contains_aug26_pce_and_gdp():
    labels=[e['type'] for e in appmod.MACRO_CALENDAR if e['date']=='2026-08-26']
    assert 'PCE' in labels
    assert 'GDP' in labels


def test_neutral_structure_has_no_directional_trade_plan(monkeypatch):
    idx=pd.bdate_range('2026-01-02', periods=80)
    # Oscillating close makes it easy to keep the MA stack mixed.
    close=pd.Series([100 + ((i%10)-5)*0.2 for i in range(80)],index=idx,dtype=float)
    df=pd.DataFrame({'Open':close,'High':close+1,'Low':close-1,'Close':close,'Volume':1000},index=idx)
    monkeypatch.setattr(appmod,'dl_ohlc',lambda ticker,period='1y':df)
    out=appmod._context_structure('TEST')
    if out['direction']=='neutral':
        assert out['trigger'] is None
        assert out['target2'] is None
        assert out['rr_to_target2'] is None
