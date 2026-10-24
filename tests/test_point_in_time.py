"""Point-in-time guarantees: no tool output may depend on data after as_of."""
import os
import sys
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tools import (MarketStore, detect_peaks, get_forward_returns, get_regime,
                   run_backtest, set_store)
from utils.data_loader import CSV_PATH, compute_panic_index, read_market_data

AS_OF_DATES = [date(2022, 3, 18), date(2022, 10, 13), date(2023, 12, 29), date(2025, 4, 9)]
MARKET_TOOLS = [
    lambda as_of: get_regime("QQQ", as_of),
    lambda as_of: run_backtest("SMH", as_of),
    lambda as_of: get_forward_returns("SPX", as_of, horizons=[5, 21, 63]),
    lambda as_of: detect_peaks("DJI", as_of, method="realtime", entry_threshold=60),
    lambda as_of: detect_peaks("QQQ", as_of, method="scipy"),
]


@pytest.fixture
def real_store():
    store = MarketStore(CSV_PATH)
    set_store(store)
    yield store
    set_store(None)


@pytest.fixture
def poisoned_csv(tmp_path):
    """Same CSV, but every row after the cutoff is replaced with extreme values."""
    def make(as_of):
        raw = pd.read_csv(CSV_PATH)
        dates = pd.to_datetime(raw['date'], dayfirst=True, errors='coerce')
        future = dates > pd.Timestamp(as_of)
        raw.loc[future, 'vix'] = 250.0
        raw.loc[future, 'fear_greed_index'] = 0.0
        for col in ['qqq_close', 'smh_close', 'spx_close', 'dji_close']:
            raw.loc[future, col] = raw.loc[future, col] * 10
        path = tmp_path / f"poisoned_{as_of}.csv"
        raw.to_csv(path, index=False)
        return path
    return make


@pytest.mark.parametrize("as_of", AS_OF_DATES)
def test_tool_outputs_ignore_data_after_as_of(as_of, poisoned_csv, real_store):
    baseline = [tool(as_of).model_dump() for tool in MARKET_TOOLS]

    set_store(MarketStore(poisoned_csv(as_of)))
    poisoned = [tool(as_of).model_dump() for tool in MARKET_TOOLS]

    assert poisoned == baseline


@pytest.mark.parametrize("as_of", AS_OF_DATES)
def test_results_report_data_through_on_or_before_as_of(as_of, real_store):
    for tool in MARKET_TOOLS:
        result = tool(as_of)
        assert result.as_of == as_of
        assert result.data_through <= as_of


def test_forward_returns_only_use_completed_windows(real_store):
    as_of = date(2022, 6, 30)
    result = get_forward_returns("QQQ", as_of, horizons=[63])
    frame = real_store.frame_as_of(as_of)
    last_signal = pd.Timestamp(result.horizons[0].last_signal_date)
    rows_after_signal = (frame['date_dt'] > last_signal).sum()
    assert rows_after_signal >= 63


def test_full_sample_normalization_would_leak():
    """The dashboard normalizes VIX with the full-sample max (2025-04-08).

    Recomputing on data truncated to an earlier date changes historical
    Panic Index values, which is why the tool layer truncates first.
    """
    as_of = pd.Timestamp("2022-10-13")
    full = compute_panic_index(read_market_data())
    truncated = compute_panic_index(read_market_data(as_of=as_of, fill_method='ffill'))
    full_value = full.loc[full['date_dt'] == as_of, 'panic_index'].iloc[0]
    pit_value = truncated.loc[truncated['date_dt'] == as_of, 'panic_index'].iloc[0]
    assert abs(full_value - pit_value) > 1.0


def test_forward_fill_never_borrows_future_prices(tmp_path):
    raw = pd.DataFrame({
        'id': [1, 2, 3],
        'date': ['3/1/2022 00:00:00', '4/1/2022 00:00:00', '5/1/2022 00:00:00'],
        'fear_greed_index': [40.0, 40.0, 40.0],
        'fear_greed_rating': ['fear'] * 3,
        'vix': [20.0, None, 40.0],
        'created_at': ['x'] * 3,
        'qqq_close': [100.0, None, 200.0],
        'smh_close': [100.0, None, 200.0],
        'spx_close': [100.0, None, 200.0],
        'dji_close': [100.0, 150.0, 200.0],  # a session with some closes missing
    })
    path = tmp_path / "gap.csv"
    raw.to_csv(path, index=False)
    df = read_market_data(path, fill_method='ffill')
    assert df['qqq_close'].tolist() == [100.0, 100.0, 200.0]
    assert df['vix'].tolist() == [20.0, 20.0, 40.0]


@pytest.mark.parametrize("fill_method", ['linear', 'ffill'])
def test_closed_market_days_are_not_rows(tmp_path, fill_method):
    """Weekends and holidays are stored with the score only; analytics skip them."""
    raw = pd.DataFrame({
        'id': [1, 2, 3, 4],
        'date': ['2/7/2026 00:00:00', '3/7/2026 00:00:00', '4/7/2026 00:00:00',
                 '6/7/2026 00:00:00'],  # Thu, Fri (holiday), Sat, Mon
        'fear_greed_index': [40.0, 41.0, 42.0, 43.0],
        'fear_greed_rating': ['fear'] * 4,
        'vix': [20.0, None, None, 22.0],
        'created_at': ['x'] * 4,
        'qqq_close': [100.0, None, None, 110.0],
        'smh_close': [100.0, None, None, 110.0],
        'spx_close': [100.0, None, None, 110.0],
        'dji_close': [100.0, None, None, 110.0],
    })
    path = tmp_path / "holiday.csv"
    raw.to_csv(path, index=False)
    df = read_market_data(path, fill_method=fill_method)
    assert df['date_only'].astype(str).tolist() == ['2026-07-02', '2026-07-06']
    assert df['qqq_return'].round(6).tolist()[1] == 10.0  # one session-to-session return


def test_as_of_before_data_start_is_a_clear_tool_error(real_store):
    with pytest.raises(ValueError, match="before the first available observation"):
        get_regime("QQQ", date(2015, 1, 1))


def test_injected_store_is_pinned_until_reset(real_store):
    from tools.market import get_store

    assert get_store() is real_store
    assert get_store() is real_store  # not swapped for a fresh default store
    set_store(None)
    assert get_store() is not real_store
