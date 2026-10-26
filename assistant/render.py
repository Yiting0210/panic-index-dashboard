"""Code-rendered figures.

The model never writes an exposure or position number. It writes a
placeholder that names the field, and code fills in the value:

* the standard signal: {current_exposure}, {previous_exposure},
  {applied_change}, {add_step}, {reduce_step}, {cap}, {floor},
  {initial_exposure};
* a run_backtest call made this turn, by the call id its result was labelled
  with: {bt1.final_position_pct}, {bt2.average_exposure_pct}, and also its
  return, drawdown, ratio, and attribution metrics ({bt1.strategy_return_pct},
  {bt1.strategy_sharpe}, {bt1.upside_drag_pp}, ...);
* a plan_position call (the user's own position today), labelled pp1, pp2,
  ...: {pp1.current_position_pct}, {pp1.applied_change_pct},
  {pp1.target_position_pct}, ...

Each field has an explicit unit: percent fields render with "%",
attribution fields in percentage points with "pp", and ratios bare. So a
figure is correct by construction, and a what-if figure is tied to the
exact call it came from. An unknown placeholder, call id, or field is
reported as an error and left in the text unrendered.

The standard backtest's own metrics (the Strategy Signal panel's returns
and drawdowns) have no placeholders; the model copies them as text.
"""
import re
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][\w]*)(?:\.([A-Za-z_]\w*))?\}")

SIGNAL_PLACEHOLDERS = {
    "current_exposure": lambda s: s.final_position_pct,
    "previous_exposure": lambda s: s.previous_position_pct,
    "applied_change": lambda s: abs(s.final_position_pct - s.previous_position_pct),
    "add_step": lambda s: s.add_pct,
    "reduce_step": lambda s: s.reduce_pct,
    "cap": lambda s: s.max_position_pct,
    "floor": lambda s: s.min_position_pct,
    "initial_exposure": lambda s: s.initial_position_pct,
}


def format_pct(value):
    return f"{float(value):g}%"


def format_pp(value):
    return f"{float(value):g}pp"


def format_ratio(value):
    return f"{float(value):g}"


# Display-safe scalar run_backtest fields and how each renders.
CALL_FIELDS = {
    # exposure levels and steps
    "final_position_pct": format_pct,
    "previous_position_pct": format_pct,
    "initial_position_pct": format_pct,
    "add_pct": format_pct,
    "reduce_pct": format_pct,
    "max_position_pct": format_pct,
    "min_position_pct": format_pct,
    "average_exposure_pct": format_pct,
    "time_below_full_exposure_pct": format_pct,
    # returns and drawdowns
    "strategy_return_pct": format_pct,
    "buyhold_return_pct": format_pct,
    "strategy_max_drawdown_pct": format_pct,
    "buyhold_max_drawdown_pct": format_pct,
    # attribution, in percentage points
    "upside_drag_pp": format_pp,
    "downside_cushion_pp": format_pp,
    # unitless ratios
    "strategy_sharpe": format_ratio,
    "buyhold_sharpe": format_ratio,
    "strategy_calmar": format_ratio,
    "buyhold_calmar": format_ratio,
}


def format_abs_pct(value):
    return format_pct(abs(float(value)))


# Display-safe plan_position fields. The applied change renders as a
# magnitude, like {applied_change}; the action word gives the direction.
PLAN_FIELDS = {
    "current_position_pct": format_pct,
    "target_position_pct": format_pct,
    "applied_change_pct": format_abs_pct,
    "add_pct": format_pct,
    "reduce_pct": format_pct,
    "max_position_pct": format_pct,
    "min_position_pct": format_pct,
}

# Call-id prefix -> (tool, allowed fields).
CALL_KINDS = {"bt": ("run_backtest", CALL_FIELDS), "pp": ("plan_position", PLAN_FIELDS)}
CALL_ID_RE = re.compile(r"^(bt|pp)\d+$")


def call_kind(call_id):
    """("run_backtest" | "plan_position", fields) for a call id, or None."""
    m = CALL_ID_RE.match(call_id or "")
    return CALL_KINDS[m.group(1)] if m else None


@dataclass
class Rendered:
    text: str
    spans: list = field(default_factory=list)  # (start, end) of each rendered value
    errors: list = field(default_factory=list)
    count: int = 0
    # Call id each span was rendered from (None for the standard signal).
    sources: list = field(default_factory=list)


def render(text, signal=None, calls=None):
    """Replace placeholders in ``text``.

    ``calls`` maps call id -> result for this turn's labelled calls:
    run_backtest (bt1, bt2, ...) and plan_position (pp1, pp2, ...).
    """
    calls = calls or {}
    out, spans, sources, errors, last = [], [], [], [], 0
    length = 0
    for m in PLACEHOLDER_RE.finditer(text):
        name, attr = m.group(1), m.group(2)
        value, formatter = None, format_pct
        kind = call_kind(name) if attr is not None else None
        if attr is None:
            if name not in SIGNAL_PLACEHOLDERS:
                errors.append(f"unknown placeholder {m.group(0)}; use one of "
                              + ", ".join("{" + n + "}" for n in SIGNAL_PLACEHOLDERS))
            elif signal is None:
                errors.append(f"{m.group(0)} has no strategy signal to render from")
            else:
                value = SIGNAL_PLACEHOLDERS[name](signal)
        elif kind is None or name not in calls:
            tool = kind[0] if kind else "run_backtest or plan_position"
            known = ", ".join(sorted(calls)) or "none"
            errors.append(f"{m.group(0)} names an unknown {tool} call id "
                          f"(this turn: {known})")
        elif attr not in kind[1]:
            errors.append(f"{m.group(0)}: {attr} is not one of {', '.join(kind[1])}")
        elif calls[name].get(attr) is None:
            errors.append(f"{m.group(0)}: call {name} has no value for {attr}")
        else:
            value, formatter = calls[name][attr], kind[1][attr]
        piece = text[last:m.start()]
        out.append(piece)
        length += len(piece)
        rendered = m.group(0) if value is None else formatter(value)
        if value is not None:
            spans.append((length, length + len(rendered)))
            sources.append(name if attr is not None else None)
        out.append(rendered)
        length += len(rendered)
        last = m.end()
    out.append(text[last:])
    return Rendered("".join(out), spans, errors, len(spans), sources)
