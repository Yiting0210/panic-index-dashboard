import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.narrative import build_threshold_research_summary


def make_inputs(latest_panic, strategy_curve=None, buyhold_curve=None):
    dff = pd.DataFrame({
        'date_dt': pd.date_range('2022-01-01', periods=3, freq='B'),
        'qqq_close': [100, 102, 101],
        'panic_index': [50, 55, latest_panic],
    })
    if strategy_curve is None:
        strategy_curve = [100, 104, 106]
    if buyhold_curve is None:
        buyhold_curve = [100, 103, 102]
    bt = pd.DataFrame({
        'cumulative_strategy': strategy_curve,
        'cumulative_buyhold': buyhold_curve,
    })
    return dff, bt


def build_summary(latest_panic, strategy_curve=None, buyhold_curve=None):
    dff, bt = make_inputs(latest_panic, strategy_curve, buyhold_curve)
    return build_threshold_research_summary(
        dff,
        bt,
        'qqq_close',
        'Nasdaq 100 (QQQ)',
        buy_threshold=64,
        sell_threshold=15,
    )


def build_summary_with_diagnostics(latest_panic, diagnostics):
    dff, bt = make_inputs(
        latest_panic,
        strategy_curve=[100, 98, 105],
        buyhold_curve=[100, 90, 130],
    )
    return build_threshold_research_summary(
        dff,
        bt,
        'qqq_close',
        'Nasdaq 100 (QQQ)',
        buy_threshold=64,
        sell_threshold=15,
        diagnostics=diagnostics,
    )


def build_summary_with_attribution(attribution):
    dff, bt = make_inputs(
        40,
        strategy_curve=[100, 98, 105],
        buyhold_curve=[100, 90, 130],
    )
    return build_threshold_research_summary(
        dff,
        bt,
        'qqq_close',
        'Nasdaq 100 (QQQ)',
        buy_threshold=64,
        sell_threshold=15,
        diagnostics={
            "average_exposure": 0.72,
            "time_below_full_exposure": 0.31,
        },
        attribution=attribution,
    )


def test_threshold_research_summary_classifies_panic_regime():
    summary = build_summary(70)

    assert "Panic regime" in summary
    assert "accumulation or exposure-increase environment" in summary
    assert "###" not in summary


def test_threshold_research_summary_classifies_greed_regime():
    summary = build_summary(10)

    assert "Greed regime" in summary
    assert "risk-warning regime" in summary
    assert "###" not in summary


def test_summary_neutral_regime_lower_return_better_drawdown():
    summary = build_summary(
        40,
        strategy_curve=[100, 98, 105],
        buyhold_curve=[100, 90, 130],
    )

    assert "Neutral regime" in summary
    assert "modest downside protection" in summary
    assert "substantial upside participation" in summary
    assert "extreme greed as an immediate exit signal" in summary
    assert "low-exposure episodes" in summary


def test_summary_uses_measured_exposure_diagnostics_when_available():
    summary = build_summary_with_diagnostics(
        40,
        {
            "average_exposure": 0.72,
            "time_below_full_exposure": 0.31,
        },
    )

    assert "average exposure was 72.0%" in summary
    assert "31.0% of observations were below full exposure" in summary
    assert "consistent with lower market participation" in summary
    assert "caused" not in summary.lower()


def test_summary_uses_upside_drag_attribution_when_available():
    summary = build_summary_with_attribution({
        "upside_drag": 12.4,
        "downside_cushion": 3.2,
    })

    assert "coincided with approximately 12.4 percentage points" in summary
    assert "3.2 points of downside cushion" in summary
    assert "arithmetic attribution basis" in summary
    assert "symmetric exit trigger" in summary
    assert "caused" not in summary.lower()
    assert "would have earned" not in summary.lower()


def test_summary_reflects_protective_downside_cushion_branch():
    summary = build_summary_with_attribution({
        "upside_drag": 2.1,
        "downside_cushion": 8.4,
    })

    assert "defensive trade-off" in summary
    assert "8.4 percentage points of downside cushion" in summary
    assert "2.1 points of uncaptured positive market return" in summary
    assert "proves" not in summary.lower()


def test_summary_falls_back_when_attribution_unavailable():
    summary = build_summary_with_attribution(None)

    assert "average exposure was 72.0%" in summary
    assert "arithmetic attribution basis" not in summary


def test_summary_strategy_beats_buyhold_on_return_and_drawdown():
    summary = build_summary(
        40,
        strategy_curve=[100, 95, 120],
        buyhold_curve=[100, 80, 110],
    )

    assert "improved both cumulative return and downside protection" in summary
    assert "returned 20.0%, compared with 10.0% for Buy & Hold" in summary
    assert "Sharpe / Sortino / Calmar" in summary


def test_summary_strategy_underperforms_on_return_and_drawdown():
    summary = build_summary(
        40,
        strategy_curve=[100, 70, 90],
        buyhold_curve=[100, 95, 110],
    )

    assert "underperformed Buy & Hold on both return and drawdown dimensions" in summary
    assert "de-risking rules that activated too late" in summary
    assert "drawdown attribution" in summary


def test_summary_strategy_higher_return_with_worse_drawdown():
    summary = build_summary(
        40,
        strategy_curve=[100, 70, 130],
        buyhold_curve=[100, 95, 110],
    )

    assert "higher return" in summary
    assert "greater downside risk" in summary
    assert "Sharpe / Sortino / Calmar" in summary
