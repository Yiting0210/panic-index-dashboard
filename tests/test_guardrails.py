"""Standard-backtest strategy signal and the answer validator."""
import os
import sys
from dataclasses import replace
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import (STANDARD_BACKTEST_START, ToolCall, build_strategy_signal,  # noqa: E402
                       validate_answer)
from assistant.signal import _action  # noqa: E402
from evals.consistency_eval import load_fixtures, run_offline  # noqa: E402
from utils.strategy import DEFAULT_STRATEGY, StrategyParams  # noqa: E402


@pytest.fixture(scope="module")
def panic_signal():
    # QQQ 2024-08-05: panic regime, the rule added 20% and holds 20%.
    return build_strategy_signal("QQQ", date(2024, 8, 5))


def backtest_call(**fields):
    return ToolCall(name="run_backtest", arguments={}, result=fields, is_error=False,
                    latency_ms=1.0)


def test_signal_uses_the_fixed_start_and_session_strategy(panic_signal):
    assert panic_signal.backtest_start == STANDARD_BACKTEST_START
    assert (panic_signal.regime, panic_signal.final_position_pct, panic_signal.action) == \
        ("panic", 20.0, "add")
    assert "run_backtest.final_position_pct = 20" in panic_signal.provenance

    custom = build_strategy_signal("QQQ", date(2024, 8, 5),
                                   StrategyParams(initial_position_pct=100.0))
    assert custom.backtest_start == STANDARD_BACKTEST_START
    assert custom.initial_position_pct == 100.0


@pytest.mark.parametrize("regime, position, expected", [
    ("panic", 60.0, "add"),
    ("panic", 100.0, "hold"),
    ("greed", 30.0, "reduce"),
    ("greed", 0.0, "hold"),
    ("neutral", 50.0, "hold"),
])
def test_action_respects_caps(regime, position, expected):
    assert _action(regime, position, DEFAULT_STRATEGY)[0] == expected


def test_validator_accepts_signal_figures_and_scaled_units(panic_signal):
    text = ("The rule added 20% today and now holds 20% exposure, or 2 out of 10 units. "
            "It would add another 20% if the Panic Index stays above 64.")
    assert validate_answer(text, panic_signal) == []


def test_validator_rejects_fabricated_position(panic_signal):
    violations = validate_answer("The framework suggests 60% exposure here.", panic_signal)
    assert len(violations) == 1 and "fabricated position 60%" in violations[0]
    assert validate_answer("Hold 6 of 10 units.", panic_signal)


def test_validator_allows_what_if_backtest_figures(panic_signal):
    text = "In a what-if starting at 2024-01-01, the strategy would end at 70% exposure."
    assert validate_answer(text, panic_signal)
    calls = [backtest_call(final_position_pct=70.0, average_exposure_pct=55.5)]
    assert validate_answer(text, panic_signal, calls) == []


def test_validator_rejects_contradictory_direction(panic_signal):
    violations = validate_answer("The strategy is now reducing exposure.", panic_signal)
    assert len(violations) == 1 and "contradicts" in violations[0]
    # Negated and conditional sentences describe the rule, not the current action.
    assert validate_answer("The strategy is not reducing exposure now.", panic_signal) == []
    assert validate_answer("If greed returns, the rule would now reduce exposure.",
                           panic_signal) == []


def test_hold_signal_ignores_past_tense_history(panic_signal):
    hold = replace(panic_signal, regime="neutral", action="hold", final_position_pct=0.0)
    history = "Exposure was reduced to 0% in June 2023 and the strategy currently holds 0%."
    assert validate_answer(history, hold) == []
    assert validate_answer("The strategy is currently adding exposure.", hold)


def test_offline_consistency_eval_catches_every_planted_contradiction():
    summary, rows = run_offline(load_fixtures())
    assert summary["fixtures"] == 5
    assert summary["signal_mismatches"] == 0, [r for r in rows if r["signal_mismatches"]]
    assert summary["detection_rate"] == 1.0, [r for r in rows if not r["correct"]]
    assert summary["false_positive_rate"] == 0.0, [r for r in rows if not r["correct"]]
