"""Standard-backtest strategy signal and the answer validator."""
import os
import sys
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import (STANDARD_BACKTEST_START, ToolCall, build_strategy_signal,  # noqa: E402
                       validate_answer)
from assistant.signal import _action_text  # noqa: E402
from evals.consistency_eval import load_fixtures, run_offline  # noqa: E402
from utils.strategy import StrategyParams  # noqa: E402


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


@pytest.mark.parametrize("regime, action, previous, final, expected", [
    ("panic", "add", 60.0, 80.0, "panic regime: added 20% exposure"),
    ("panic", "add", 80.0, 100.0, "panic regime: added 20% exposure, reaching the 100% cap"),
    ("panic", "add", 90.0, 100.0, "panic regime: added 10% exposure, reaching the 100% cap"),
    ("panic", "hold", 100.0, 100.0, "panic regime, already at the 100% cap"),
    ("greed", "reduce", 10.0, 0.0, "greed regime: reduced exposure by 10%, reaching the 0% floor"),
    ("greed", "hold", 0.0, 0.0, "greed regime, already at the 0% floor"),
    ("neutral", "hold", 50.0, 50.0, "neutral regime: no change"),
])
def test_action_text_uses_the_applied_step(regime, action, previous, final, expected):
    bt = SimpleNamespace(latest_regime=regime, latest_action=action,
                         final_position_pct=final, previous_position_pct=previous,
                         latest_position_change_pct=final - previous,
                         max_position_pct=100.0, min_position_pct=0.0)
    assert _action_text(bt) == expected


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


@pytest.fixture
def eighty_pct_signal(panic_signal):
    # The rule added 20% (60% -> 80%); add 20, reduce 10, cap 100, floor 0, start 50.
    return replace(panic_signal, final_position_pct=80.0, previous_position_pct=60.0)


@pytest.mark.parametrize("text", [
    "The current exposure is 20%.",            # the add step
    "The current exposure is 10%.",            # the reduce step
    "The current position is 100%.",           # the cap
    "The current exposure is 0%.",             # the floor
    "The strategy currently holds 50% exposure.",  # the initial exposure
    "The strategy now holds 60% exposure.",    # the previous exposure
    "The strategy holds 2 of 10 units.",
])
def test_current_exposure_must_match_final_position(eighty_pct_signal, text):
    violations = validate_answer(text, eighty_pct_signal)
    assert len(violations) == 1 and "read as the current exposure" in violations[0]


@pytest.mark.parametrize("text", [
    "The strategy holds 80% exposure, or 8 out of 10 units.",
    "The rule added 20% today, taking exposure from 60% to 80%.",
    "The rule adds 20% exposure per panic-regime day and reduces exposure by 10% per "
    "greed-regime day.",
    "Exposure is capped at a 100% maximum position and has a 0% exposure floor.",
    "The backtest started at 50% exposure.",
    "Exposure was reduced to 0% in June 2023.",
    "Exposure has held at its level while the index fell 12% this month.",
])
def test_figures_pass_when_they_match_their_own_field(eighty_pct_signal, text):
    assert validate_answer(text, eighty_pct_signal) == []


def test_figures_are_checked_against_their_own_field(eighty_pct_signal):
    assert "read as the add step" in validate_answer(
        "The rule added 30% exposure today.", eighty_pct_signal)[0]
    assert "read as the previous exposure" in validate_answer(
        "Exposure rose from 40% to 80%.", eighty_pct_signal)[0]
    assert "read as the exposure cap" in validate_answer(
        "Exposure is capped at 90%.", eighty_pct_signal)[0]


def test_clipped_step_matches_the_applied_change(eighty_pct_signal):
    clipped = replace(eighty_pct_signal, final_position_pct=100.0, previous_position_pct=90.0)
    assert validate_answer("The rule added 10% exposure, reaching the cap.", clipped) == []


def test_what_if_figures_match_only_backtest_calls(eighty_pct_signal):
    calls = [backtest_call(final_position_pct=70.0, add_pct=30.0, average_exposure_pct=55.5)]
    # A what-if result cannot stand in for the current exposure, and the reverse.
    assert validate_answer("The strategy currently holds 70% exposure.", eighty_pct_signal, calls)
    assert validate_answer("In the what-if the strategy would end at 80% exposure.",
                           eighty_pct_signal, calls)
    assert validate_answer("In the what-if the rule adds 30% exposure per panic day.",
                           eighty_pct_signal, calls) == []
    assert validate_answer("Over that backtest the average exposure was 55.5%.",
                           eighty_pct_signal, calls) == []
    assert validate_answer("Over that backtest the average exposure was 55.5%.",
                           eighty_pct_signal)


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
    assert summary["fixtures"] == 10
    assert summary["signal_mismatches"] == 0, [r for r in rows if r["signal_mismatches"]]
    assert summary["detection_rate"] == 1.0, [r for r in rows if not r["correct"]]
    assert summary["false_positive_rate"] == 0.0, [r for r in rows if not r["correct"]]
