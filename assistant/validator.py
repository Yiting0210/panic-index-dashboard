"""Format checks on the model's explanation of the code-computed signal.

Position figures are code-rendered (assistant/render.py): the model writes
{current_exposure} or {bt1.final_position_pct} and code fills in the value.
So this module does not try to understand what a number means. It checks
the format contract and the one structured field:

* raw position figure: a percentage the model wrote itself (not produced by
  a placeholder) in the same clause as a position word (exposure, position,
  allocation, invested), or any "N of M units". Returns, drawdowns, and
  index levels are fine as long as they sit in their own clause;
* current_action: the action the model says it describes must be the
  signal's action;
* contradictory direction (backstop): an unhedged sentence about the
  current action that says add while the rule reduces or holds, or the
  reverse. Negated or conditional sentences ("would add if...") are skipped.
"""
import re

from .render import Rendered

ACTIONS = ("add", "reduce", "hold")

POSITION_WORD_RE = re.compile(
    r"\b(?:exposures?|positions?|allocations?|invested|units?)\b", re.IGNORECASE)
PCT_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE)
UNITS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:out of|of|/)\s*\d+\s*units?\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_BREAK_RE = re.compile(
    r"[,;:()]|\b(?:and|but|while|so|which|then|whereas|versus|vs\.?|compared)\b",
    re.IGNORECASE)

# "Buy & Hold" names the benchmark; its "buy" is not an add action.
BUY_HOLD_RE = re.compile(r"\bbuy[\s-]*(?:&|and)[\s-]*hold\b", re.IGNORECASE)
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


def _segments(text, pattern):
    """(start, end) of the pieces of ``text`` between matches of ``pattern``."""
    start = 0
    for m in pattern.finditer(text):
        yield start, m.start()
        start = m.end()
    yield start, len(text)


def raw_position_figures(rendered):
    """Position figures the model wrote itself instead of using a placeholder."""
    if isinstance(rendered, str):
        rendered = Rendered(rendered)
    text = rendered.text

    def is_rendered(start, end):
        return any(s <= start and end <= e for s, e in rendered.spans)

    found = []
    for s_start, s_end in _segments(text, SENTENCE_RE):
        sentence = text[s_start:s_end].strip()
        for c_start, c_end in _segments(text[s_start:s_end], CLAUSE_BREAK_RE):
            a, b = s_start + c_start, s_start + c_end
            if not POSITION_WORD_RE.search(text[a:b]):
                continue
            for m in PCT_RE.finditer(text, a, b):
                if not is_rendered(m.start(), m.end()):
                    found.append((m.group(0).strip(), sentence))
        for m in UNITS_RE.finditer(text, s_start, s_end):
            found.append((m.group(0), sentence))
    return found


def parse_current_action(data):
    """(current_action, errors) from a final answer, validated strictly.

    Missing or "not_stated" gives None; any other value outside add / reduce
    / hold is an error, so a malformed field never silently skips the check.
    """
    action = data.get("current_action")
    if action is None or action == "not_stated":
        return None, []
    if action in ACTIONS:
        return action, []
    error = (f"invalid structured output: current_action {action!r} is not one of "
             "add, reduce, hold, not_stated")
    return None, [error]


def stated_actions(text, present_only=False):
    """(action, sentence) for unhedged sentences about the current action."""
    add_re, reduce_re = (ADD_NOW_RE, REDUCE_NOW_RE) if present_only else (ADD_ANY_RE, REDUCE_ANY_RE)
    found = []
    for sentence in SENTENCE_RE.split(text):
        if not (CURRENT_RE.search(sentence) and TOPIC_RE.search(sentence)):
            continue
        if HEDGE_RE.search(sentence):
            continue
        verbs = BUY_HOLD_RE.sub(" ", sentence)
        adds, reduces = bool(add_re.search(verbs)), bool(reduce_re.search(verbs))
        if adds != reduces:
            found.append(("add" if adds else "reduce", sentence.strip()))
    return found


def validate_answer(rendered, signal, current_action=None):
    """Violations of the format contract and the signal; empty when consistent.

    ``rendered`` is one Rendered text (or a plain string with no rendered
    figures), or a list of them, one per answer field.
    """
    parts = rendered if isinstance(rendered, list) else [rendered]
    parts = [Rendered(p) if isinstance(p, str) else p for p in parts]
    violations = []
    for part in parts:
        violations += [f"placeholder error: {e}" for e in part.errors]
        for figure, sentence in raw_position_figures(part):
            violations.append(
                f"raw position figure {figure}: write position figures only as "
                f"placeholders such as {{current_exposure}}: {sentence}")
    if signal is None:
        return violations
    if current_action is not None and current_action != signal.action:
        violations.append(f"current_action '{current_action}' contradicts the rule's "
                          f"'{signal.action}' ({signal.regime} regime)")
    text = "\n".join(p.text for p in parts)
    for action, sentence in stated_actions(text, present_only=signal.action == "hold"):
        if action != signal.action:
            violations.append(
                f"direction '{action}' contradicts the rule's '{signal.action}' "
                f"({signal.regime} regime): {sentence}")
    return violations
