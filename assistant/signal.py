"""The strategy signal, computed by code from one standard backtest.

``final_position_pct`` is path dependent: it changes with the backtest's start
date and initial exposure. So the assistant never lets the model pick them.
Every answer is anchored on one backtest with a fixed start date and the
session's strategy settings, and the Market Regime and Strategy Signal panels
are filled from that result. The model only explains them.

That exposure is the strategy path's, not a personal target: someone who
holds a different position today gets the rule's next step from
``tools.plan_position`` instead (see ``position_plans``).
"""
from dataclasses import dataclass
from datetime import date
from typing import Literal

from tools import get_regime, plan_position, run_backtest
from utils.strategy import DEFAULT_STRATEGY

# First observation in the dataset; every standard backtest starts here.
STANDARD_BACKTEST_START = date(2021, 5, 17)

Action = Literal["add", "reduce", "hold"]


@dataclass(frozen=True)
class StrategySignal:
    market: str
    as_of: date
    observation_date: date
    regime: str
    panic_index: float
    panic_index_percentile: float
    buy_threshold: float
    sell_threshold: float
    backtest_start: date
    final_position_pct: float
    previous_position_pct: float
    action: Action
    action_text: str
    next_rule: str
    position_rule: str
    add_pct: float
    reduce_pct: float
    min_position_pct: float
    max_position_pct: float
    initial_position_pct: float
    strategy_return_pct: float
    buyhold_return_pct: float
    strategy_max_drawdown_pct: float
    buyhold_max_drawdown_pct: float

    @property
    def provenance(self):
        """Which tool fields the panels come from ("Why this signal?")."""
        return (
            f"run_backtest.final_position_pct = {self.final_position_pct:g}, "
            f"run_backtest.latest_action = {self.action} "
            f"(from {self.previous_position_pct:g}%), "
            f"run_backtest.latest_regime = {self.regime} "
            f"(Panic Index {self.panic_index:g} vs. thresholds "
            f"{self.buy_threshold:g} / {self.sell_threshold:g}), "
            f"standard backtest {self.backtest_start.isoformat()} to "
            f"{self.observation_date.isoformat()}"
        )

    def prompt_block(self):
        """The signal as given to the model: facts to explain, not to change."""
        return (
            "Strategy signal (computed by code from the standard backtest and shown "
            "to the user above your answer; do not restate it with different numbers):\n"
            f"- market: {self.market}; latest observation: {self.observation_date.isoformat()}\n"
            f"- regime: {self.regime}; Panic Index {self.panic_index:g} "
            f"(percentile {self.panic_index_percentile:g})\n"
            f"- strategy path exposure (final_position_pct): {self.final_position_pct:g}, "
            f"reached by following the rule continuously since "
            f"{self.backtest_start.isoformat()} (previous_position_pct before the latest "
            f"observation: {self.previous_position_pct:g}); not a target for someone "
            f"holding a different position today (plan_position covers that)\n"
            f"- action on the latest observation: {self.action} ({self.action_text})\n"
            f"- next session: {self.next_rule}\n"
            f"- position_rule: {self.position_rule}\n"
            f"- standard backtest window: {self.backtest_start.isoformat()} to "
            f"{self.observation_date.isoformat()}; strategy return "
            f"{self.strategy_return_pct:g}% vs. Buy & Hold {self.buyhold_return_pct:g}%"
        )


def _action_text(bt):
    """Describe the backtest's latest transition; the action itself comes from code.

    Uses the step actually applied, which is smaller than add_pct / reduce_pct
    when the position was clipped at the cap or floor.
    """
    regime, change, final = bt.latest_regime, bt.latest_position_change_pct, bt.final_position_pct
    if bt.latest_action == "add":
        text = f"{regime} regime: added {change:g}% exposure"
        return text + (f", reaching the {bt.max_position_pct:g}% cap"
                       if final >= bt.max_position_pct else "")
    if bt.latest_action == "reduce":
        text = f"{regime} regime: reduced exposure by {-change:g}%"
        return text + (f", reaching the {bt.min_position_pct:g}% floor"
                       if final <= bt.min_position_pct else "")
    if regime == "panic" and final >= bt.max_position_pct:
        return f"panic regime, already at the {bt.max_position_pct:g}% cap"
    if regime == "greed" and final <= bt.min_position_pct:
        return f"greed regime, already at the {bt.min_position_pct:g}% floor"
    return f"{regime} regime: no change"


def build_strategy_signal(market, as_of, strategy=None):
    """Run the standard backtest and regime check for ``market`` as of ``as_of``."""
    params = strategy or DEFAULT_STRATEGY
    settings = params.as_dict()
    bt = run_backtest(market, as_of, start_date=STANDARD_BACKTEST_START, **settings)
    regime = get_regime(market, as_of, buy_threshold=params.buy_threshold,
                        sell_threshold=params.sell_threshold)
    next_rule = (
        f"the strategy path holds {bt.final_position_pct:g}%; adds {params.add_pct:g}% if the Panic Index "
        f"closes above {params.buy_threshold:g}, reduces {params.reduce_pct:g}% if it "
        f"closes below {params.sell_threshold:g}, within {params.min_position_pct:g}%-"
        f"{params.max_position_pct:g}%"
    )
    return StrategySignal(
        market=market,
        as_of=bt.as_of,
        observation_date=bt.data_through,
        regime=bt.latest_regime,
        panic_index=bt.latest_panic_index,
        panic_index_percentile=regime.panic_index_percentile,
        buy_threshold=params.buy_threshold,
        sell_threshold=params.sell_threshold,
        backtest_start=bt.start_date,
        final_position_pct=bt.final_position_pct,
        previous_position_pct=bt.previous_position_pct,
        action=bt.latest_action,
        action_text=_action_text(bt),
        next_rule=next_rule,
        position_rule=bt.position_rule,
        add_pct=params.add_pct,
        reduce_pct=params.reduce_pct,
        min_position_pct=params.min_position_pct,
        max_position_pct=params.max_position_pct,
        initial_position_pct=params.initial_position_pct,
        strategy_return_pct=bt.strategy_return_pct,
        buyhold_return_pct=bt.buyhold_return_pct,
        strategy_max_drawdown_pct=bt.strategy_max_drawdown_pct,
        buyhold_max_drawdown_pct=bt.buyhold_max_drawdown_pct,
    )


# Starting positions in the page's "Decision From Your Current Position" table.
COMPARISON_POSITIONS = (0.0, 20.0, 50.0, 80.0, 100.0)


def position_plans(market, as_of, positions=COMPARISON_POSITIONS, strategy=None):
    """plan_position for each starting position, with the session's settings.

    Positions outside the rule's min/max range are left out.
    """
    params = strategy or DEFAULT_STRATEGY
    settings = {k: v for k, v in params.as_dict().items() if k != "initial_position_pct"}
    return [plan_position(market, as_of, current_position_pct=p, **settings)
            for p in positions
            if params.min_position_pct <= p <= params.max_position_pct]
