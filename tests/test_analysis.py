import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.analysis import build_correlation_evidence_summary


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
