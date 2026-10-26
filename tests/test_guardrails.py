"""Standard-backtest strategy signal, placeholder rendering, and format checks."""
import os
import sys
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import (STANDARD_BACKTEST_START, build_strategy_signal, render,  # noqa: E402
                       validate_answer)
from assistant.signal import _action_text  # noqa: E402
from assistant.validator import parse_current_action  # noqa: E402
from evals.consistency_eval import load_fixtures, run_offline  # noqa: E402
from utils.strategy import StrategyParams  # noqa: E402


@pytest.fixture(scope="module")
def panic_signal():
    # QQQ 2024-08-05: panic regime, the rule added 20% and holds 20%.
    return build_strategy_signal("QQQ", date(2024, 8, 5))


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


def check(text, signal, backtests=None, current_action=None):
    """Render like the agent does, then validate."""
    return validate_answer(render(text, signal, backtests), signal, current_action)


# Placeholder rendering.
def test_signal_placeholders_render_from_the_signal(panic_signal):
    out = render("Added {applied_change}, now {current_exposure}, step {add_step}, "
                 "cap {cap}, floor {floor}, start {initial_exposure}, was {previous_exposure}.",
                 panic_signal)
    assert out.errors == [] and out.count == 7
    assert out.text == ("Added 20%, now 20%, step 20%, cap 100%, floor 0%, start 50%, "
                        "was 0%.")


def test_backtest_placeholders_render_from_the_named_call(panic_signal):
    backtests = {"bt1": {"final_position_pct": 70.0, "average_exposure_pct": 81.0},
                 "bt2": {"final_position_pct": 40.0, "average_exposure_pct": 52.5}}
    out = render("A ends at {bt1.final_position_pct}; B ends at {bt2.final_position_pct} "
                 "and averaged {bt2.average_exposure_pct}.", panic_signal, backtests)
    assert out.errors == []
    assert out.text == "A ends at 70%; B ends at 40% and averaged 52.5%."


@pytest.mark.parametrize("text, error", [
    ("Now {current_position}.", "unknown placeholder"),
    ("Ends at {bt3.final_position_pct}.", "unknown run_backtest call id"),
    ("Ends at {bt1.verdict}.", "is not one of"),  # text field, not a display scalar
    ("Ends at {bt1.previous_position_pct}.", "has no value"),
])
def test_bad_placeholders_are_errors(panic_signal, text, error):
    out = render(text, panic_signal, {"bt1": {"final_position_pct": 70.0}})
    assert len(out.errors) == 1 and error in out.errors[0]
    assert check(text, panic_signal, {"bt1": {"final_position_pct": 70.0}})


def test_signal_placeholder_without_a_signal_is_an_error():
    assert render("Now {current_exposure}.").errors


# Format contract: position figures come only from placeholders.
@pytest.mark.parametrize("text", [
    "Current exposure is 64%.",          # #28: 64 is the buy threshold
    "Current exposure is 99.3%.",        # #28: the percentile
    "The rule now holds 20% exposure.",  # even the right value must be a placeholder
    "The strategy is 35% invested.",
    "That is 2 out of 10 units.",
    "Exposure is {current_exposure}, or 20 percent of capital invested.",
    # Another percentage in a clause about exposure breaks the contract too.
    "Holding {current_exposure} exposure returned 4.24% over the window.",
])
def test_raw_position_figures_are_rejected(panic_signal, text):
    violations = check(text, panic_signal)
    assert violations and any("raw position figure" in v for v in violations)


@pytest.mark.parametrize("text, backtests", [
    ("The rule added {applied_change} and now holds {current_exposure} exposure.", None),
    # Phrasings from the live DeepSeek runs, written with placeholders.
    ("The latest observation's action was 'add': exposure rose {applied_change} from "
     "{previous_exposure} to {current_exposure}, reaching the {cap} cap.", None),
    ("Buy & Hold returned 14.71% (strategy 5.73%), with {bt1.average_exposure_pct} average "
     "exposure and 35.12% max drawdown.", {"bt1": {"average_exposure_pct": 90.3}}),
    ("The panel shows a neutral reading, holding {current_exposure} exposure; the strategy "
     "returned 4.24% vs. Buy & Hold 11.3%.", None),
    ("The standard backtest holds {current_exposure} exposure and trails Buy & Hold, "
     "3.88% vs 31.16%.", None),
    ("The reduction in average exposure ({bt1.average_exposure_pct}) left the strategy "
     "behind.", {"bt1": {"average_exposure_pct": 59.8}}),
    ("In a what-if with a 60 threshold the strategy would end at "
     "{bt1.final_position_pct} exposure.", {"bt1": {"final_position_pct": 70.0}}),
    # Percentages with no position word in their clause are not position figures.
    ("The Panic Index is at the 99.3rd percentile, and 63-day returns averaged -1.0%.", None),
])
def test_placeholder_answers_pass(panic_signal, text, backtests):
    assert check(text, panic_signal, backtests, current_action="add") == []


# Issue #31 A: strategy-state phrasings without a position word.
@pytest.mark.parametrize("text", [
    "The rule holds 64%.",
    "The strategy currently holds 64%.",
    "The signal is at 64%.",
    "The framework remains at 20%.",
    "The strategy is still at 64%.",
    "The signal holds at the 100% cap.",
    # Live run 5, a10: no position word in the clause.
    "QQQ remains neutral (Panic Index 38.84), so the rule holds 100%; a close above 64 "
    "would add.",
])
def test_strategy_state_phrasings_need_placeholders(panic_signal, text):
    violations = check(text, panic_signal)
    assert any("raw position figure" in v for v in violations), violations


@pytest.mark.parametrize("text", [
    "The rule holds {current_exposure}.",
    "The strategy currently holds {current_exposure}.",
    "The signal is at {current_exposure}.",
    "The framework remains at {floor}.",
    "The signal holds at the {cap} cap.",
])
def test_strategy_state_phrasings_with_placeholders_pass(panic_signal, text):
    assert check(text, panic_signal, current_action="add") == []


@pytest.mark.parametrize("text", [
    "It holds 64%.",                          # pronouns are not covered (documented)
    "The strategy returned 64% over the window.",
    "The strategy is at a 35% drawdown.",
    "The Panic Index is at 64%.",
])
def test_strategy_state_rule_stays_narrow(panic_signal, text):
    assert not any("raw position figure" in v for v in check(text, panic_signal))


# Issue #31 B: run_backtest numeric placeholders with explicit units.
A12_BACKTEST = {"bt1": {"strategy_return_pct": 42.19, "buyhold_return_pct": 95.79,
                        "strategy_max_drawdown_pct": -4.24, "buyhold_max_drawdown_pct": -13.56,
                        "upside_drag_pp": 167.66, "downside_cushion_pp": 133.15,
                        "strategy_sharpe": 2.138, "buyhold_sharpe": 1.973,
                        "strategy_calmar": 9.95, "buyhold_calmar": 7.06,
                        "average_exposure_pct": 29.5, "time_below_full_exposure_pct": 100.0,
                        "reduce_pct": 10.0}}


def test_backtest_metric_placeholders_render_with_their_units(panic_signal):
    out = render("Returned {bt1.strategy_return_pct} vs {bt1.buyhold_return_pct}; drawdown "
                 "{bt1.strategy_max_drawdown_pct}; drag {bt1.upside_drag_pp} vs cushion "
                 "{bt1.downside_cushion_pp}; Sharpe {bt1.strategy_sharpe}, Calmar "
                 "{bt1.buyhold_calmar}.", panic_signal, A12_BACKTEST)
    assert out.errors == []
    assert out.text == ("Returned 42.19% vs 95.79%; drawdown -4.24%; drag 167.66pp vs cushion "
                        "133.15pp; Sharpe 2.138, Calmar 7.06.")


def test_a12_attribution_renders_but_a_raw_step_is_still_rejected(panic_signal):
    # Live run 5, a12: the attribution placeholders were rejected as unsupported
    # fields; now they render. Its raw "10%" reduce step is still a violation.
    rendered = ("Upside drag was {bt1.upside_drag_pp} against a downside cushion of "
                "{bt1.downside_cushion_pp}, with average exposure of "
                "{bt1.average_exposure_pct}.")
    assert check(rendered, panic_signal, A12_BACKTEST, current_action="add") == []
    raw_step = "Each greed day trimmed exposure by 10%."
    assert any("raw position figure 10%" in v
               for v in check(raw_step, panic_signal, A12_BACKTEST))
    assert check("Each greed day trimmed exposure by {bt1.reduce_pct}.", panic_signal,
                 A12_BACKTEST) == []


def test_current_action_must_match_the_signal(panic_signal):
    assert check("Holding steady.", panic_signal, current_action="reduce")
    assert parse_current_action({"current_action": "add"}) == ("add", [])
    assert parse_current_action({"current_action": "not_stated"}) == (None, [])
    assert parse_current_action({}) == (None, [])
    action, errors = parse_current_action({"current_action": "buy"})
    assert action is None and "invalid structured output" in errors[0]


def test_validator_rejects_contradictory_direction(panic_signal):
    violations = validate_answer("The strategy is now reducing exposure.", panic_signal)
    assert len(violations) == 1 and "contradicts" in violations[0]
    # Negated and conditional sentences describe the rule, not the current action.
    assert validate_answer("The strategy is not reducing exposure now.", panic_signal) == []
    assert validate_answer("If greed returns, the rule would now reduce exposure.",
                           panic_signal) == []


def test_hold_signal_ignores_past_tense_history(panic_signal):
    hold = replace(panic_signal, regime="neutral", action="hold", final_position_pct=0.0)
    history = ("Exposure was reduced to {floor} in June 2023 and the strategy currently "
               "holds {current_exposure}.")
    assert check(history, hold) == []
    assert validate_answer("The strategy is currently adding exposure.", hold)


def test_offline_consistency_eval_catches_every_planted_contradiction():
    summary, rows = run_offline(load_fixtures())
    assert summary["fixtures"] == 10
    assert summary["signal_mismatches"] == 0, [r for r in rows if r["signal_mismatches"]]
    assert summary["detection_rate"] == 1.0, [r for r in rows if not r["correct"]]
    assert summary["false_positive_rate"] == 0.0, [r for r in rows if not r["correct"]]
