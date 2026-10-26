"""The rule applied once to the user's own position (#33), and its placeholders."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from assistant import build_strategy_signal, position_plans, render, validate_answer  # noqa: E402
from tools import plan_position  # noqa: E402
from tools.market import ToolInputError  # noqa: E402
from utils.strategy import StrategyParams, apply_rule_once  # noqa: E402

PANIC_DAY = date(2024, 8, 5)      # QQQ panic regime; the strategy path added 0% -> 20%
CAP_DAY = date(2025, 3, 6)        # QQQ panic regime; the strategy path holds at the 100% cap
GREED_DAY = date(2023, 6, 16)     # QQQ greed regime; the strategy path reduced 10% -> 0%
NEUTRAL_DAY = date(2026, 10, 23)  # QQQ neutral regime


@pytest.mark.parametrize("regime, position, expected", [
    ("panic", 0.0, ("add", 20.0, 20.0)),
    ("panic", 50.0, ("add", 20.0, 70.0)),
    ("panic", 90.0, ("add", 10.0, 100.0)),   # clipped at the cap
    ("panic", 100.0, ("hold", 0.0, 100.0)),  # already at the cap
    ("greed", 50.0, ("reduce", -10.0, 40.0)),
    ("greed", 5.0, ("reduce", -5.0, 0.0)),   # clipped at the floor
    ("greed", 0.0, ("hold", 0.0, 0.0)),      # already at the floor
    ("neutral", 0.0, ("hold", 0.0, 0.0)),
    ("neutral", 50.0, ("hold", 0.0, 50.0)),
    ("neutral", 100.0, ("hold", 0.0, 100.0)),
])
def test_apply_rule_once(regime, position, expected):
    assert apply_rule_once(position, regime) == expected


def test_apply_rule_once_uses_the_given_settings():
    params = StrategyParams(add_pct=30.0, reduce_pct=25.0, max_position_pct=80.0,
                            min_position_pct=10.0)
    assert apply_rule_once(60.0, "panic", params) == ("add", 20.0, 80.0)
    assert apply_rule_once(30.0, "greed", params) == ("reduce", -20.0, 10.0)


@pytest.mark.parametrize("as_of, position, action, change, target", [
    (PANIC_DAY, 0.0, "add", 20.0, 20.0),
    (PANIC_DAY, 90.0, "add", 10.0, 100.0),
    (PANIC_DAY, 100.0, "hold", 0.0, 100.0),
    (GREED_DAY, 50.0, "reduce", -10.0, 40.0),
    (GREED_DAY, 5.0, "reduce", -5.0, 0.0),
    (GREED_DAY, 0.0, "hold", 0.0, 0.0),
    (NEUTRAL_DAY, 0.0, "hold", 0.0, 0.0),
    (NEUTRAL_DAY, 50.0, "hold", 0.0, 50.0),
])
def test_plan_position_applies_the_latest_regime_once(as_of, position, action, change, target):
    plan = plan_position("QQQ", as_of, current_position_pct=position)
    assert (plan.action, plan.applied_change_pct, plan.target_position_pct) == \
        (action, change, target)
    assert plan.current_position_pct == position
    assert plan.data_through <= as_of


@pytest.mark.parametrize("as_of", [PANIC_DAY, CAP_DAY, GREED_DAY, date(2023, 6, 20), NEUTRAL_DAY])
def test_plan_from_the_paths_previous_position_matches_the_backtest_step(as_of):
    # Same mechanic: one step from where the path stood reproduces its latest transition.
    signal = build_strategy_signal("QQQ", as_of)
    plan = plan_position("QQQ", as_of, current_position_pct=signal.previous_position_pct)
    assert plan.regime == signal.regime
    assert (plan.action, plan.target_position_pct) == (signal.action, signal.final_position_pct)


def test_plan_ignores_the_strategy_path_position():
    # The path is at the 100% cap and holds; someone at 0% or 50% still adds.
    signal = build_strategy_signal("QQQ", CAP_DAY)
    assert (signal.action, signal.final_position_pct) == ("hold", 100.0)
    plans = {p.current_position_pct: p for p in position_plans("QQQ", CAP_DAY)}
    assert sorted(plans) == [0.0, 20.0, 50.0, 80.0, 100.0]
    assert [(plans[p].action, plans[p].target_position_pct) for p in sorted(plans)] == [
        ("add", 20.0), ("add", 40.0), ("add", 70.0), ("add", 100.0), ("hold", 100.0)]


def test_next_rule_names_the_cap_and_floor():
    at_cap = plan_position("QQQ", CAP_DAY, current_position_pct=100.0)
    assert "already at the 100% cap" in at_cap.next_rule
    assert "reduce 10% if it closes below 15" in at_cap.next_rule
    near_cap = plan_position("QQQ", NEUTRAL_DAY, current_position_pct=90.0)
    assert "add 10% if the Panic Index closes above 64" in near_cap.next_rule
    at_floor = plan_position("QQQ", GREED_DAY, current_position_pct=0.0)
    assert "already at the 0% floor" in at_floor.next_rule


def test_plan_rejects_positions_outside_the_rule_range():
    with pytest.raises(ToolInputError):
        plan_position("QQQ", NEUTRAL_DAY, current_position_pct=90.0, max_position_pct=80.0)
    with pytest.raises(ToolInputError):
        plan_position("QQQ", NEUTRAL_DAY, current_position_pct=50.0,
                      min_position_pct=60.0, max_position_pct=40.0)


def test_position_plans_use_session_settings_and_skip_out_of_range_rows():
    params = StrategyParams(add_pct=30.0, max_position_pct=80.0)
    plans = position_plans("QQQ", CAP_DAY, strategy=params)
    assert [p.current_position_pct for p in plans] == [0.0, 20.0, 50.0, 80.0]
    assert [p.target_position_pct for p in plans] == [30.0, 50.0, 80.0, 80.0]


# Placeholders and format checks.
def plans_for(as_of, *positions):
    return {f"pp{i}": plan_position("QQQ", as_of, current_position_pct=p).model_dump()
            for i, p in enumerate(positions, 1)}


def check(text, signal, calls):
    plans = {k: v for k, v in calls.items() if k.startswith("pp")}
    return validate_answer(render(text, signal, calls), signal, plans=plans)


def test_plan_placeholders_render_from_the_named_call():
    calls = plans_for(GREED_DAY, 50.0, 5.0)
    out = render("From {pp1.current_position_pct} the rule reduces {pp1.applied_change_pct} "
                 "to {pp1.target_position_pct}; from {pp2.current_position_pct} it ends at "
                 "{pp2.target_position_pct}.", None, calls)
    assert out.errors == []
    assert out.text == "From 50% the rule reduces 10% to 40%; from 5% it ends at 0%."
    assert out.sources == ["pp1"] * 3 + ["pp2"] * 2


@pytest.mark.parametrize("text, error", [
    ("Ends at {pp3.target_position_pct}.", "unknown plan_position call id"),
    ("Ends at {pp1.final_position_pct}.", "is not one of"),  # a backtest field
    ("Ends at {pp1.next_rule}.", "is not one of"),           # text, not a figure
    ("Ends at {xx1.target_position_pct}.", "unknown run_backtest or plan_position call id"),
])
def test_bad_plan_placeholders_are_errors(text, error):
    out = render(text, None, plans_for(GREED_DAY, 50.0))
    assert len(out.errors) == 1 and error in out.errors[0]


def test_your_position_sentences_are_checked_against_the_plan_not_the_path():
    signal = build_strategy_signal("QQQ", CAP_DAY)  # path holds at the cap
    calls = plans_for(CAP_DAY, 50.0)                # from 50% the rule adds
    text = ("The strategy path holds {current_exposure} exposure at the cap. From your "
            "{pp1.current_position_pct} position the rule adds {pp1.applied_change_pct} "
            "today, taking you to {pp1.target_position_pct}.")
    assert check(text, signal, calls) == []
    # Without the plan figures the same sentence is about the path, which holds.
    assert check("From your position the rule adds exposure today.", signal, calls)


def test_your_position_sentence_contradicting_the_plan_is_rejected():
    signal = build_strategy_signal("QQQ", CAP_DAY)
    calls = plans_for(CAP_DAY, 100.0)  # already at the cap: hold
    violations = check("From your {pp1.current_position_pct} position the rule is adding "
                       "exposure today.", signal, calls)
    assert violations and "from your position (pp1)" in violations[0]


def test_raw_user_position_figures_are_still_rejected():
    signal = build_strategy_signal("QQQ", CAP_DAY)
    violations = check("If your position is 50% today, the rule takes it to 70%.", signal, {})
    assert any("raw position figure" in v for v in violations)
