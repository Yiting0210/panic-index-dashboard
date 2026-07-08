import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.analysis import (
    build_correlation_evidence_summary,
    build_exposure_tradeoff_attribution,
    build_strategy_diagnostics,
)
from utils.metrics import build_performance_metrics, sharpe_ratio


def build_market_frame(x_values, y_values):
    return pd.DataFrame({
        'fear_greed_index': x_values,
        'price_dev_pct': y_values,
    })


def get_first_summary_row(x_values, y_values):
    summary = build_correlation_evidence_summary(
        {'market': build_market_frame(x_values, y_values)},
        [('market', 'Test Market')],
    )
    return summary.iloc[0]


def test_correlation_summary_perfect_positive_relationship():
    row = get_first_summary_row([1, 2, 3, 4], [10, 20, 30, 40])

    assert row["Pearson r"] == pytest.approx(1)
    assert row["Spearman ρ"] == pytest.approx(1)
    assert row["N"] == 4


def test_correlation_summary_perfect_negative_relationship():
    row = get_first_summary_row([1, 2, 3, 4], [40, 30, 20, 10])

    assert row["Pearson r"] == pytest.approx(-1)
    assert row["Spearman ρ"] == pytest.approx(-1)
    assert row["N"] == 4


def test_correlation_summary_excludes_missing_pairs():
    row = get_first_summary_row([1, 2, None, 4], [2, None, 6, 8])

    assert row["Pearson r"] == pytest.approx(1)
    assert row["Spearman ρ"] == pytest.approx(1)
    assert row["N"] == 2


def test_correlation_summary_handles_constant_input_without_crashing():
    row = get_first_summary_row([1, 1, 1, 1], [2, 3, 4, 5])

    assert pd.isna(row["Pearson r"])
    assert pd.isna(row["Spearman ρ"])
    assert row["N"] == 4


def make_backtest_frame():
    return pd.DataFrame({
        'date_dt': pd.to_datetime([
            '2022-01-01',
            '2022-07-01',
            '2023-01-01',
        ]),
        'position': [1.0, 0.5, 0.75],
        'strategy_return': [0.01, -0.02, 0.03],
        'daily_return': [0.02, -0.01, 0.01],
        'cumulative_strategy': [100, 80, 120],
        'cumulative_buyhold': [100, 90, 110],
    })


def test_strategy_diagnostics_average_exposure_and_time_below_full():
    diagnostics = build_strategy_diagnostics(make_backtest_frame())

    assert diagnostics["average_exposure"] == pytest.approx(0.75)
    assert diagnostics["time_below_full_exposure"] == pytest.approx(2 / 3)


def test_performance_metrics_sharpe_matches_canonical_function():
    bt = make_backtest_frame()
    metrics = build_performance_metrics(bt)

    assert metrics["strategy_sharpe"] == pytest.approx(
        sharpe_ratio(
            bt['strategy_return'],
            bt['date_dt'],
            periods_per_year=metrics["periods_per_year"],
        )
    )
    assert metrics["buyhold_sharpe"] == pytest.approx(
        sharpe_ratio(
            bt['daily_return'],
            bt['date_dt'],
            periods_per_year=metrics["periods_per_year"],
        )
    )


def test_performance_metrics_use_same_annualization_convention():
    metrics = build_performance_metrics(make_backtest_frame())

    assert metrics["periods_per_year"] > 0
    assert metrics["annual_risk_free_rate"] == 0.0


def test_performance_metrics_handles_zero_volatility_sharpe():
    bt = make_backtest_frame()
    bt['strategy_return'] = 0.01

    metrics = build_performance_metrics(bt)

    assert pd.isna(metrics["strategy_sharpe"])


def test_performance_metrics_handles_insufficient_sharpe_data():
    bt = make_backtest_frame().iloc[:1].copy()

    metrics = build_performance_metrics(bt)

    assert pd.isna(metrics["strategy_sharpe"])
    assert pd.isna(metrics["buyhold_sharpe"])


def test_performance_metrics_calmar_known_example():
    bt = make_backtest_frame()
    metrics = build_performance_metrics(bt)
    elapsed_days = (bt['date_dt'].iloc[-1] - bt['date_dt'].iloc[0]).days
    expected_annualized = (120 / 100) ** (365.25 / elapsed_days) - 1
    expected_calmar = expected_annualized / 0.20

    assert metrics["strategy_calmar"] == pytest.approx(expected_calmar)


def test_performance_metrics_handles_zero_drawdown_calmar():
    bt = make_backtest_frame()
    bt['cumulative_strategy'] = [100, 110, 120]

    metrics = build_performance_metrics(bt)

    assert pd.isna(metrics["strategy_calmar"])


def make_attribution_frame(positions, returns):
    return pd.DataFrame({
        'date_dt': pd.date_range('2022-01-01', periods=len(positions), freq='B'),
        'position': positions,
        'daily_return': returns,
    })


def test_exposure_attribution_uses_lagged_effective_exposure():
    bt = make_attribution_frame([1.0, 0.5], [0.0, 0.10])

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["upside_drag"] == pytest.approx(0.0)


def test_exposure_attribution_positive_return_adds_upside_drag():
    bt = make_attribution_frame([0.5, 0.5], [None, 0.10])

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["upside_drag"] == pytest.approx(5.0)
    assert attribution["downside_cushion"] == pytest.approx(0.0)


def test_exposure_attribution_negative_return_adds_downside_cushion():
    bt = make_attribution_frame([0.5, 0.5], [None, -0.10])

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["upside_drag"] == pytest.approx(0.0)
    assert attribution["downside_cushion"] == pytest.approx(5.0)


def test_exposure_attribution_full_exposure_is_zero():
    bt = make_attribution_frame([1.0, 1.0, 1.0], [None, 0.10, -0.10])

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["upside_drag"] == pytest.approx(0.0)
    assert attribution["downside_cushion"] == pytest.approx(0.0)


def test_exposure_attribution_handles_first_observation_and_missing_values():
    bt = make_attribution_frame([0.5, None, 0.5, 0.5], [0.10, 0.10, None, 0.10])

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["upside_drag"] == pytest.approx(20.0)
    assert attribution["downside_cushion"] == pytest.approx(0.0)


def test_exposure_attribution_episode_survives_return_sign_change():
    bt = make_attribution_frame(
        [1.0, 0.5, 0.5, 0.5, 1.0, 0.5, 0.5],
        [None, 0.10, -0.05, 0.08, 0.10, 0.20, 0.01],
    )

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["largest_episode_start"] == pd.Timestamp("2022-01-05")
    assert attribution["largest_episode_end"] == pd.Timestamp("2022-01-07")
    assert attribution["largest_episode_upside_drag"] == pytest.approx(9.0)
    assert attribution["largest_episode_downside_cushion"] == pytest.approx(2.5)


def test_exposure_attribution_largest_episode_ranks_by_upside_drag():
    bt = make_attribution_frame(
        [1.0, 0.5, 0.5, 1.0, 0.2, 0.2],
        [None, 0.10, 0.04, 0.0, 0.01, 0.10],
    )

    attribution = build_exposure_tradeoff_attribution(bt)

    assert attribution["largest_episode_start"] == pd.Timestamp("2022-01-10")
    assert attribution["largest_episode_end"] == pd.Timestamp("2022-01-10")
    assert attribution["largest_episode_upside_drag"] == pytest.approx(8.0)
