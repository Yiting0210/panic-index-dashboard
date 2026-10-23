# tests/test_strategy.py
import pandas as pd
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import run_backtest, max_drawdown, sharpe_ratio

def make_dummy_data():
    """Create minimal test DataFrame."""
    dates = pd.date_range('2022-01-01', periods=100, freq='B')
    np.random.seed(42)
    prices = 100 * (1 + np.random.randn(100) * 0.01).cumprod()
    panic  = np.random.uniform(0, 100, 100)
    return pd.DataFrame({
        'date_dt':     dates,
        'qqq_close':   prices,
        'panic_index': panic,
    })

def test_backtest_returns_correct_columns():
    dff = make_dummy_data()
    bt = run_backtest(dff, 'qqq_close')
    assert 'cumulative_strategy' in bt.columns
    assert 'cumulative_buyhold' in bt.columns
    assert 'position' in bt.columns

def test_position_never_exceeds_max():
    dff = make_dummy_data()
    bt = run_backtest(dff, 'qqq_close', pos_max=80)
    assert bt['position'].max() <= 0.80 + 1e-9

def test_position_never_below_min():
    dff = make_dummy_data()
    bt = run_backtest(dff, 'qqq_close', pos_min=20)
    assert bt['position'].min() >= 0.20 - 1e-9

def test_backtest_uses_custom_thresholds():
    dff = pd.DataFrame({
        'date_dt': pd.date_range('2022-01-01', periods=4, freq='B'),
        'qqq_close': [100, 101, 102, 103],
        'panic_index': [50, 65, 10, 50],
    })

    bt_default = run_backtest(
        dff,
        'qqq_close',
        pos_initial=50,
        add_amount=20,
        reduce_amount=20,
    )
    bt_custom = run_backtest(
        dff,
        'qqq_close',
        pos_initial=50,
        add_amount=20,
        reduce_amount=20,
        buy_threshold=70,
        sell_threshold=5,
    )

    np.testing.assert_allclose(
        bt_default['position'].to_numpy(),
        [0.5, 0.7, 0.5, 0.5],
    )
    np.testing.assert_allclose(
        bt_custom['position'].to_numpy(),
        [0.5, 0.5, 0.5, 0.5],
    )

def test_max_drawdown_negative():
    series = pd.Series([100, 110, 90, 95, 105])
    dd = max_drawdown(series)
    assert dd < 0

def test_sharpe_ratio_positive_for_uptrend():
    returns = pd.Series([0.01, 0.011] * 126)
    sr = sharpe_ratio(returns)
    assert sr > 0

if __name__ == "__main__":
    test_backtest_returns_correct_columns()
    test_position_never_exceeds_max()
    test_position_never_below_min()
    test_max_drawdown_negative()
    test_sharpe_ratio_positive_for_uptrend()
    print("All tests passed ✅")


def test_default_rule_matches_the_dashboard_defaults():
    from utils.strategy import DEFAULT_STRATEGY

    dff = pd.DataFrame({
        'date_dt': pd.date_range('2022-01-01', periods=3, freq='B'),
        'qqq_close': [100, 101, 102],
        'panic_index': [70, 10, 50],
    })
    positions = run_backtest(dff, 'qqq_close')['position'].tolist()
    start = DEFAULT_STRATEGY.initial_position_pct
    assert positions == [
        (start + DEFAULT_STRATEGY.add_pct) / 100,
        (start + DEFAULT_STRATEGY.add_pct - DEFAULT_STRATEGY.reduce_pct) / 100,
        (start + DEFAULT_STRATEGY.add_pct - DEFAULT_STRATEGY.reduce_pct) / 100,
    ]


def test_backtest_tool_reports_the_rule_behind_its_position():
    import pytest
    from tools.core import run_backtest as backtest_tool
    from tools.market import ToolInputError

    result = backtest_tool("QQQ", "2022-10-13", add_pct=30, max_position_pct=80)
    assert result.final_position_pct <= 80
    assert (result.add_pct, result.reduce_pct, result.max_position_pct) == (30, 10, 80)
    assert "add 30% per panic-regime day" in result.position_rule
    assert result.latest_regime in {"panic", "greed", "neutral"}
    with pytest.raises(ToolInputError):
        backtest_tool("QQQ", "2022-10-13", min_position_pct=60, max_position_pct=50)
