"""Checks the model's prose against the code-computed strategy signal.

The Market Regime and Strategy Signal panels are filled by code, so the model
only writes explanations. This validator rejects an explanation that
contradicts the panels:

* fabricated allocation: an exposure / position percentage (or "N of M
  units") that matches neither the standard backtest nor a run_backtest call
  the model made this turn (e.g. a what-if);
* contradictory direction: a sentence about the current action that says
  add while the rule reduces or holds, or the reverse.

Sentences that are negated or conditional ("would add if...") are skipped,
so describing the rule itself is allowed.
"""
import re

POSITION_WORDS = r"(?:exposure|position|allocation|invested)"
PCT = r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)"
# "exposure of 80%", "position is now 60 percent", "80% exposure", "60% of capital invested".
PCT_AFTER_WORD_RE = re.compile(POSITION_WORDS + r"\b[^.%\d]{0,40}?" + PCT, re.IGNORECASE)
PCT_BEFORE_WORD_RE = re.compile(PCT + r"\s+(?:\w+\s+){0,2}?" + POSITION_WORDS + r"\b",
                                re.IGNORECASE)
UNITS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:out of|of|/)\s*(\d+)\s*units?\b", re.IGNORECASE)

CURRENT_RE = re.compile(
    r"\b(?:now|currently|today|right now|at present|next session|latest observation|"
    r"this session)\b", re.IGNORECASE)
TOPIC_RE = re.compile(r"\b(?:exposure|position|allocation|rule|strategy|framework)\b",
                      re.IGNORECASE)
HEDGE_RE = re.compile(
    r"\b(?:not|no|nothing|never|neither|nor|without|would|could|if|unless|whether)\b|n't\b",
    re.IGNORECASE)
# Present-tense verbs state what the rule does now; past-tense ones ("was
# reduced in 2023") may be history, so they only count against add/reduce.
ADD_NOW = r"add(?:s|ing)?|increas(?:e|es|ing)|rais(?:e|es|ing)|accumulat(?:e|es|ing)|buy(?:s|ing)?"
ADD_PAST = r"added|increased|raised|accumulated|bought"
REDUCE_NOW = (r"reduc(?:e|es|ing)|trim(?:s|ming)?|cut(?:s|ting)?|lower(?:s|ing)?|"
              r"sell(?:s|ing)?|de-?risk(?:s|ing)?")
REDUCE_PAST = r"reduced|trimmed|lowered|sold|de-?risked"
ADD_NOW_RE = re.compile(rf"\b(?:{ADD_NOW})\b", re.IGNORECASE)
ADD_ANY_RE = re.compile(rf"\b(?:{ADD_NOW}|{ADD_PAST})\b", re.IGNORECASE)
REDUCE_NOW_RE = re.compile(rf"\b(?:{REDUCE_NOW})\b", re.IGNORECASE)
REDUCE_ANY_RE = re.compile(rf"\b(?:{REDUCE_NOW}|{REDUCE_PAST})\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# run_backtest fields that state an exposure level or step.
POSITION_FIELDS = ("final_position_pct", "initial_position_pct", "add_pct", "reduce_pct",
                   "max_position_pct", "min_position_pct", "average_exposure_pct",
                   "time_below_full_exposure_pct")


def allowed_positions(signal, tool_calls=()):
    """Exposure figures the answer may state: the signal's and any backtest's."""
    allowed = {0.0, signal.final_position_pct, signal.initial_position_pct, signal.add_pct,
               signal.reduce_pct, signal.max_position_pct, signal.min_position_pct}
    for call in tool_calls:
        if call.name == "run_backtest" and call.result and not call.is_error:
            allowed.update(float(call.result[f]) for f in POSITION_FIELDS
                           if call.result.get(f) is not None)
    return allowed


def stated_positions(text):
    """Exposure percentages stated in ``text`` (units converted to percent)."""
    values = [float(m) for m in PCT_AFTER_WORD_RE.findall(text)]
    values += [float(m) for m in PCT_BEFORE_WORD_RE.findall(text)]
    for count, total in UNITS_RE.findall(text):
        if float(total) > 0:
            values.append(float(count) / float(total) * 100)
    return values


def stated_actions(text, present_only=False):
    """(action, sentence) for unhedged sentences about the current action."""
    add_re, reduce_re = (ADD_NOW_RE, REDUCE_NOW_RE) if present_only else (ADD_ANY_RE, REDUCE_ANY_RE)
    found = []
    for sentence in SENTENCE_RE.split(text):
        if not (CURRENT_RE.search(sentence) and TOPIC_RE.search(sentence)):
            continue
        if HEDGE_RE.search(sentence):
            continue
        adds, reduces = bool(add_re.search(sentence)), bool(reduce_re.search(sentence))
        if adds != reduces:
            found.append(("add" if adds else "reduce", sentence.strip()))
    return found


def validate_answer(text, signal, tool_calls=(), tolerance=0.51):
    """Violations of the strategy signal in ``text``; empty when consistent."""
    violations = []
    allowed = allowed_positions(signal, tool_calls)
    for value in sorted(set(stated_positions(text))):
        if not any(abs(value - ok) <= tolerance for ok in allowed):
            violations.append(
                f"fabricated position {value:g}% (standard backtest final_position_pct "
                f"is {signal.final_position_pct:g}%)"
            )
    for action, sentence in stated_actions(text, present_only=signal.action == "hold"):
        if action != signal.action:
            violations.append(
                f"direction '{action}' contradicts the rule's '{signal.action}' "
                f"({signal.regime} regime): {sentence}"
            )
    return violations
