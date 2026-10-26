"""Code-rendered position figures.

The model never writes an exposure or position number. It writes a
placeholder that names the field, and code fills in the value:

* the standard signal: {current_exposure}, {previous_exposure},
  {applied_change}, {add_step}, {reduce_step}, {cap}, {floor},
  {initial_exposure};
* a run_backtest call made this turn, by the call id its result was labelled
  with: {bt1.final_position_pct}, {bt2.average_exposure_pct}, ...

So a position figure is correct by construction, and a what-if figure is
tied to the exact call it came from. An unknown placeholder, call id, or
field is reported as an error and left in the text unrendered.
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
# run_backtest result fields that state an exposure level or step.
CALL_FIELDS = ("final_position_pct", "previous_position_pct", "initial_position_pct",
               "add_pct", "reduce_pct", "max_position_pct", "min_position_pct",
               "average_exposure_pct", "time_below_full_exposure_pct")


@dataclass
class Rendered:
    text: str
    spans: list = field(default_factory=list)  # (start, end) of each rendered value
    errors: list = field(default_factory=list)
    count: int = 0


def format_pct(value):
    return f"{float(value):g}%"


def render(text, signal=None, backtests=None):
    """Replace placeholders in ``text``.

    ``backtests`` maps call id -> run_backtest result for this turn.
    """
    backtests = backtests or {}
    out, spans, errors, last = [], [], [], 0
    length = 0
    for m in PLACEHOLDER_RE.finditer(text):
        name, attr = m.group(1), m.group(2)
        value = None
        if attr is None:
            if name not in SIGNAL_PLACEHOLDERS:
                errors.append(f"unknown placeholder {m.group(0)}; use one of "
                              + ", ".join("{" + n + "}" for n in SIGNAL_PLACEHOLDERS))
            elif signal is None:
                errors.append(f"{m.group(0)} has no strategy signal to render from")
            else:
                value = SIGNAL_PLACEHOLDERS[name](signal)
        elif name not in backtests:
            known = ", ".join(sorted(backtests)) or "none"
            errors.append(f"{m.group(0)} names an unknown run_backtest call id "
                          f"(this turn: {known})")
        elif attr not in CALL_FIELDS:
            errors.append(f"{m.group(0)}: {attr} is not one of {', '.join(CALL_FIELDS)}")
        elif backtests[name].get(attr) is None:
            errors.append(f"{m.group(0)}: call {name} has no value for {attr}")
        else:
            value = backtests[name][attr]
        piece = text[last:m.start()]
        out.append(piece)
        length += len(piece)
        rendered = m.group(0) if value is None else format_pct(value)
        if value is not None:
            spans.append((length, length + len(rendered)))
        out.append(rendered)
        length += len(rendered)
        last = m.end()
    out.append(text[last:])
    return Rendered("".join(out), spans, errors, len(spans))
