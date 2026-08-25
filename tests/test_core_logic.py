"""
Regression tests for the classification logic that's easy to get subtly
wrong: post-earnings directional persistence/reversion (PEAD) and STRAT
scenario classification.

These use synthetic, deterministic price data and monkeypatch dl_ohlc() —
no network calls, no API keys required.

Run with:  pytest tests/test_core_logic.py -v
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod


# ---------------------------------------------------------------------------
# Synthetic OHLC helpers
# ---------------------------------------------------------------------------

def _business_days(n, start="2023-01-02"):
    return pd.bdate_range(start=start, periods=n)


def _make_flat_ohlc(n=300, base_price=100.0):
    """A flat baseline OHLC frame (no drift, no noise) for event injection."""
    idx = _business_days(n)
    close = pd.Series(base_price, index=idx, dtype=float)
    return pd.DataFrame(
        {"Open": close.values, "High": close.values, "Low": close.values, "Close": close.values},
        index=idx,
    )


def _apply_event(df, event_pos, pop_pct, path_pct_per_day, path_days):
    """Simulate an earnings event at event_pos: an immediate pop_pct jump on
    the event day's close, followed by path_days sessions each compounding
    path_pct_per_day off the prior close. The resulting level is held flat
    until the next event overwrites it."""
    close = df["Close"].copy()
    level = close.iloc[event_pos - 1] * (1 + pop_pct / 100.0)
    close.iloc[event_pos] = level
    for i in range(1, path_days + 1):
        level = level * (1 + path_pct_per_day / 100.0)
        if event_pos + i < len(close):
            close.iloc[event_pos + i] = level
    if event_pos + path_days + 1 < len(close):
        close.iloc[event_pos + path_days + 1:] = level
    df["Close"] = close.values
    df["Open"] = close.values
    df["High"] = close.values
    df["Low"] = close.values
    return df


def _synthetic_earnings_series(behavior, n_events=4, spacing=45, pop_pct=8.0):
    """Build a synthetic OHLC frame with n_events earnings-like jumps, each
    exhibiting either continuation or reversion behavior over the following
    sessions, plus the earnings dates to feed into earnings_profile()."""
    total_days = spacing * (n_events + 1) + 20
    df = _make_flat_ohlc(total_days)
    dates = []
    for k in range(1, n_events + 1):
        event_pos = spacing * k
        if behavior == "continuation":
            # Keeps drifting the same direction as the initial pop.
            _apply_event(df, event_pos, pop_pct=pop_pct, path_pct_per_day=0.6, path_days=14)
        elif behavior == "reversion":
            # Gives back essentially the whole initial pop within the window.
            _apply_event(df, event_pos, pop_pct=pop_pct, path_pct_per_day=-0.9, path_days=10)
        else:
            raise ValueError(behavior)
        dates.append(df.index[event_pos])
    return df, dates


class _FakeDlOhlc:
    """Swaps in a pre-built synthetic frame wherever dl_ohlc() is called."""
    def __init__(self, frame):
        self.frame = frame

    def __call__(self, ticker, period="4y"):
        return self.frame


# ---------------------------------------------------------------------------
# earnings_profile() — directional persistence / reversion (PEAD proxy)
# ---------------------------------------------------------------------------

def test_earnings_profile_classifies_continuation(monkeypatch):
    df, dates = _synthetic_earnings_series("continuation")
    monkeypatch.setattr(appmod, "dl_ohlc", _FakeDlOhlc(df))
    profile = appmod.earnings_profile("TEST", dates)
    assert profile is not None
    assert profile["behavior"] == "CONTINUATION"
    assert profile["pct_directional_persist"] >= 60.0


def test_earnings_profile_classifies_reversion(monkeypatch):
    df, dates = _synthetic_earnings_series("reversion")
    monkeypatch.setattr(appmod, "dl_ohlc", _FakeDlOhlc(df))
    profile = appmod.earnings_profile("TEST", dates)
    assert profile is not None
    assert profile["behavior"] == "REVERSION"
    assert profile["pct_directional_revert"] >= 45.0


def test_earnings_profile_has_exc14_data_flag_is_explicit(monkeypatch):
    # Regression guard for the v23.3 fix: has_exc14_data must be an explicit
    # boolean derived from real data presence, not inferred from whether
    # median_exc14 happens to be truthy (which breaks if the median rounds
    # to exactly 0.0 for a genuinely quiet mover).
    df, dates = _synthetic_earnings_series("continuation")
    monkeypatch.setattr(appmod, "dl_ohlc", _FakeDlOhlc(df))
    profile = appmod.earnings_profile("TEST", dates)
    assert profile is not None
    assert profile["has_exc14_data"] is True


def test_earnings_profile_returns_none_with_insufficient_events(monkeypatch):
    # Fewer than 3 completed events should bail out rather than classify off
    # a too-small sample.
    df, dates = _synthetic_earnings_series("continuation", n_events=2)
    monkeypatch.setattr(appmod, "dl_ohlc", _FakeDlOhlc(df))
    profile = appmod.earnings_profile("TEST", dates)
    assert profile is None


def test_earnings_profile_returns_none_with_too_little_history(monkeypatch):
    # len(df) < 80 should bail out regardless of how many dates are passed.
    short_df = _make_flat_ohlc(40)
    monkeypatch.setattr(appmod, "dl_ohlc", _FakeDlOhlc(short_df))
    profile = appmod.earnings_profile("TEST", [short_df.index[10]])
    assert profile is None


# ---------------------------------------------------------------------------
# _strat_scenario() — STRAT bar classification
# ---------------------------------------------------------------------------

def _bar(high, low, close=None, open_=None):
    return {
        "High": high, "Low": low,
        "Close": close if close is not None else (high + low) / 2,
        "Open": open_ if open_ is not None else (high + low) / 2,
    }


def test_strat_scenario_inside_bar():
    prev = _bar(110, 100)
    cur = _bar(108, 102)  # fully inside prev's range
    assert appmod._strat_scenario(prev, cur) == "1"


def test_strat_scenario_outside_bar():
    prev = _bar(110, 100)
    cur = _bar(115, 95)  # breaks both sides
    assert appmod._strat_scenario(prev, cur) == "3"


def test_strat_scenario_directional_2u():
    prev = _bar(110, 100)
    cur = _bar(112, 101)  # breaks the high only
    assert appmod._strat_scenario(prev, cur) == "2U"


def test_strat_scenario_directional_2d():
    prev = _bar(110, 100)
    cur = _bar(109, 98)  # breaks the low only
    assert appmod._strat_scenario(prev, cur) == "2D"


def test_strat_scenario_matching_range_is_inside():
    # A bar that exactly matches the prior bar's high/low satisfies the
    # inside-bar condition (ch<=ph and cl>=pl), not an ambiguous case.
    prev = _bar(110, 100)
    cur = _bar(110, 100, close=109, open_=101)
    assert appmod._strat_scenario(prev, cur) == "1"
