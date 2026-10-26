"""Decision facts: the strategy decisions code owns, for the UI and the model.

One object holds every critical strategy fact of a question: the strategy
path (the standard backtest's exposure and latest action) and, when the user
entered a position on the page, the rule's step from that position
(``tools.plan_position``). The page renders these facts directly, and the
same object is given to the model as read-only context. The model explains
them; it never supplies the user's position or restates the decision, so
phrasing cannot change it.
"""
from dataclasses import dataclass
from typing import Optional

from tools import plan_position
from tools.schemas import PositionPlanResult
from utils.strategy import DEFAULT_STRATEGY

from .signal import StrategySignal, build_strategy_signal


def boundary_state(plan):
    """How the cap or floor shaped a plan_position step, or None.

    "clipped at the cap": the add was smaller than add_pct because it hit the cap;
    "reached the cap": a full add landed exactly on it; "already at the cap": a
    panic-regime hold. The same three for the floor.
    """
    if plan.action == "add" and plan.target_position_pct >= plan.max_position_pct:
        clipped = plan.applied_change_pct < plan.add_pct
        return f"{'clipped at' if clipped else 'reached'} the {plan.max_position_pct:g}% cap"
    if plan.action == "reduce" and plan.target_position_pct <= plan.min_position_pct:
        clipped = -plan.applied_change_pct < plan.reduce_pct
        return f"{'clipped at' if clipped else 'reached'} the {plan.min_position_pct:g}% floor"
    if plan.action == "hold" and plan.regime == "panic" and plan.target_position_pct >= plan.max_position_pct:
        return f"already at the {plan.max_position_pct:g}% cap"
    if plan.action == "hold" and plan.regime == "greed" and plan.target_position_pct <= plan.min_position_pct:
        return f"already at the {plan.min_position_pct:g}% floor"
    return None


@dataclass(frozen=True)
class DecisionFacts:
    signal: StrategySignal
    # The rule's step from the position the user entered on the page.
    plan: Optional[PositionPlanResult] = None

    @property
    def plan_boundary(self):
        return boundary_state(self.plan) if self.plan is not None else None

    def as_dict(self):
        """Every decision field, for eval scoring and the debug view."""
        out = {"strategy_path": {
            "regime": self.signal.regime,
            "exposure_pct": self.signal.final_position_pct,
            "previous_pct": self.signal.previous_position_pct,
            "action": self.signal.action,
            "action_text": self.signal.action_text,
            "next_rule": self.signal.next_rule,
        }}
        if self.plan is not None:
            out["your_position"] = {
                "current_position_pct": self.plan.current_position_pct,
                "action": self.plan.action,
                "applied_change_pct": self.plan.applied_change_pct,
                "target_position_pct": self.plan.target_position_pct,
                "boundary": self.plan_boundary,
                "next_rule": self.plan.next_rule,
            }
        return out

    def prompt_block(self):
        """The decision as given to the model: authoritative facts to explain."""
        text = self.signal.prompt_block()
        if self.plan is not None:
            p = self.plan
            boundary = f"; {self.plan_boundary}" if self.plan_boundary else ""
            text += (
                "\n\nYour-position decision (the user entered their current exposure on the "
                "page; computed by code with plan_position and shown to the user):\n"
                f"- current position: {p.current_position_pct:g}%\n"
                f"- today's action: {p.action} ({p.applied_change_pct:+g}%{boundary})\n"
                f"- position after today's rule: {p.target_position_pct:g}%\n"
                f"- next: {p.next_rule}"
            )
        return text + (
            "\n\nThese decision facts are authoritative and already shown to the user. "
            "Explain why they are what they are; do not recalculate, reinterpret, override, "
            "or restate them.")


def build_decision(market, as_of, strategy=None, current_position_pct=None):
    """Decision facts for ``market`` as of ``as_of``.

    ``current_position_pct`` comes from the page input, never from the model.
    """
    params = strategy or DEFAULT_STRATEGY
    signal = build_strategy_signal(market, as_of, params)
    plan = None
    if current_position_pct is not None:
        settings = {k: v for k, v in params.as_dict().items() if k != "initial_position_pct"}
        plan = plan_position(market, as_of, current_position_pct=current_position_pct,
                             **settings)
    return DecisionFacts(signal=signal, plan=plan)
