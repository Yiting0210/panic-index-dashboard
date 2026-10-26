"""Threshold strategy parameters shared by the dashboard and the research tools.

The Threshold Strategy page, the backtest helpers, and the MCP tools all read
their defaults from ``DEFAULT_STRATEGY``, so a setting changed here changes
everywhere. Percentages are 0-100.
"""
from dataclasses import asdict, dataclass

SESSION_KEY = "strategy_params"


@dataclass(frozen=True)
class StrategyParams:
    buy_threshold: float = 64.0
    sell_threshold: float = 15.0
    initial_position_pct: float = 50.0
    add_pct: float = 20.0
    reduce_pct: float = 10.0
    max_position_pct: float = 100.0
    min_position_pct: float = 0.0

    def as_dict(self):
        return asdict(self)

    def describe(self):
        return (
            f"panic regime above {self.buy_threshold:g}, greed regime below "
            f"{self.sell_threshold:g}; start at {self.initial_position_pct:g}% exposure, "
            f"add {self.add_pct:g}% per panic-regime day, reduce {self.reduce_pct:g}% "
            f"per greed-regime day, between {self.min_position_pct:g}% and "
            f"{self.max_position_pct:g}%"
        )


DEFAULT_STRATEGY = StrategyParams()


def apply_rule_once(position_pct, regime, params=DEFAULT_STRATEGY):
    """(action, applied_change_pct, target_pct) for one observation of ``regime``.

    The same step the backtest applies each day: add ``add_pct`` in the
    panic regime, remove ``reduce_pct`` in the greed regime, hold otherwise,
    clipped to the min/max exposure. The action is read from the change, so
    a step clipped to zero at the cap or floor is a hold.
    """
    if regime == "panic":
        target = min(position_pct + params.add_pct, params.max_position_pct)
    elif regime == "greed":
        target = max(position_pct - params.reduce_pct, params.min_position_pct)
    else:
        target = position_pct
    target = round(target, 1)
    change = round(target - position_pct, 1)
    action = "add" if change > 0 else "reduce" if change < 0 else "hold"
    return action, change, target
