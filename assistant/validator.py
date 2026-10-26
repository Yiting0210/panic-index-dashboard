"""Format checks on the model's explanation of the code-computed signal.

Position figures are code-rendered (assistant/render.py): the model writes
{current_exposure} or {bt1.final_position_pct} and code fills in the value.
So this module does not try to understand what a number means. It checks
the format contract and the one structured field:

* raw position figure: a percentage the model wrote itself (not produced by
  a placeholder) in the same clause as a position word (exposure, position,
  allocation, invested), or any "N of M units". Returns, drawdowns, and
  index levels are fine as long as they sit in their own clause. A few fixed
  strategy-state phrasings count too, without a position word: rule /
  strategy / framework / signal + (up to two adverbs) + holds [at (the)] /
  is at / sits at / remains at / stays at + N%. Pronouns ("it holds 64%")
  and other phrasings are not covered; this is a finite format rule, not
  language understanding;
* current_action: the action the model says it describes must be the
  signal's action;
* contradictory direction (backstop): an unhedged sentence about the
  current action that says add while the rule reduces or holds, or the
  reverse. Negated or conditional sentences ("would add if...") are skipped.
  A sentence with a figure rendered from a plan_position call is about the
  user's own position, so it is checked against that call's action instead
  of the standard signal's (from 50% the rule adds while the strategy path,
  already at the cap, holds). An unhedged sentence about the rule's action
  today from "your" position with no such figure is a violation, since
  there is no plan to check it against.
"""
import re

from .render import Rendered

ACTIONS = ("add", "reduce", "hold")

POSITION_WORD_RE = re.compile(
    r"\b(?:exposures?|positions?|allocations?|invested|units?)\b", re.IGNORECASE)
PCT_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE)
UNITS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:out of|of|/)\s*\d+\s*units?\b", re.IGNORECASE)
# "the rule holds 64%", "the strategy currently holds at the 100%",
# "the signal is at 64%": strategy-state phrasings that state a position
# without a position word. Deliberately narrow; no pronouns.
STATE_SUBJECT = r"\b(?:rule|strategy|framework|signal)(?:'s)?"
STATE_ADVERB = r"(?:\w+ly|now|still|already|again|also)"
# "is at", "is still at", "remains at": an adverb may also sit inside the verb.
STATE_VERB = (rf"(?:holds?(?:\s+at(?:\s+the)?)?|(?:is|sits?|remains?|stays?)\s+"
              rf"(?:{STATE_ADVERB}\s+){{0,2}}at)")
STATE_FIGURE_RE = re.compile(
    rf"{STATE_SUBJECT}\s+(?:{STATE_ADVERB}\s+){{0,2}}{STATE_VERB}\s+"
    r"(-?\d+(?:\.\d+)?\s*(?:%|percent\b))", re.IGNORECASE)
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
# "your position", "you're 50% invested": a sentence about the user's own
# position. Deliberately narrow: a bare "you" ("this tells you...") is not.
YOUR_RE = re.compile(r"\byour\b|\byou(?:'re|\s+are|\s+hold|\s+have|\s+own)\b", re.IGNORECASE)
HOLD_RE = re.compile(r"\b(?:hold(?:s|ing)?|stay(?:s|ing)?|keep(?:s|ing)?|remain(?:s|ing)?|"
                     r"unchanged)\b", re.IGNORECASE)
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
        figures = {}  # start -> figure, so a figure both rules catch counts once
        for c_start, c_end in _segments(text[s_start:s_end], CLAUSE_BREAK_RE):
            a, b = s_start + c_start, s_start + c_end
            if not POSITION_WORD_RE.search(text[a:b]):
                continue
            for m in PCT_RE.finditer(text, a, b):
                figures[m.start()] = (m.end(), m.group(0).strip())
        for m in STATE_FIGURE_RE.finditer(text, s_start, s_end):
            figures[m.start(1)] = (m.end(1), m.group(1).strip())
        for start, (end, figure) in sorted(figures.items()):
            if not is_rendered(start, end):
                found.append((figure, sentence))
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


def _stated_action(sentence, present_only):
    """add / reduce for an unhedged sentence about the current action, else None."""
    if not (CURRENT_RE.search(sentence) and TOPIC_RE.search(sentence)):
        return None
    if HEDGE_RE.search(sentence):
        return None
    add_re, reduce_re = (ADD_NOW_RE, REDUCE_NOW_RE) if present_only else (ADD_ANY_RE, REDUCE_ANY_RE)
    verbs = BUY_HOLD_RE.sub(" ", sentence)
    adds, reduces = bool(add_re.search(verbs)), bool(reduce_re.search(verbs))
    if adds == reduces:
        return None
    return "add" if adds else "reduce"


def _unplanned_your_action(sentence):
    """Whether an unhedged sentence states the rule's action today from the
    user's own position (add, reduce, or hold)."""
    if not (YOUR_RE.search(sentence) and CURRENT_RE.search(sentence)
            and TOPIC_RE.search(sentence)) or HEDGE_RE.search(sentence):
        return False
    verbs = BUY_HOLD_RE.sub(" ", sentence)
    return bool(ADD_ANY_RE.search(verbs) or REDUCE_ANY_RE.search(verbs) or HOLD_RE.search(verbs))


def _plan_sources(rendered, start, end):
    """Ids of plan_position calls whose figures were rendered in text[start:end]."""
    return {source for (s, e), source in zip(rendered.spans, rendered.sources)
            if source and source.startswith("pp") and start <= s and e <= end}


def _direction_violations(parts, signal, plans):
    violations = []
    for part in parts:
        for start, end in _segments(part.text, SENTENCE_RE):
            sentence = part.text[start:end].strip()
            ids = _plan_sources(part, start, end)
            if ids:
                expected = {plans[i]["action"] for i in ids if i in plans}
                action = _stated_action(sentence, present_only=expected == {"hold"})
                if action and action not in expected:
                    names = ", ".join(sorted(ids))
                    violations.append(
                        f"direction '{action}' contradicts the rule's "
                        f"'{'/'.join(sorted(expected))}' from your position ({names}): "
                        f"{sentence}")
            elif _unplanned_your_action(sentence):
                # Without a plan figure it would be checked against the
                # strategy path, which is a different position.
                violations.append(
                    "your-position action without a plan_position figure: call "
                    "plan_position for that position and write {ppN.current_position_pct} "
                    f"/ {{ppN.target_position_pct}}: {sentence}")
            elif signal is not None:
                action = _stated_action(sentence, present_only=signal.action == "hold")
                if action and action != signal.action:
                    violations.append(
                        f"direction '{action}' contradicts the rule's '{signal.action}' "
                        f"({signal.regime} regime): {sentence}")
    return violations


def validate_answer(rendered, signal, current_action=None, plans=None):
    """Violations of the format contract and the signal; empty when consistent.

    ``rendered`` is one Rendered text (or a plain string with no rendered
    figures), or a list of them, one per answer field. ``plans`` maps
    plan_position call ids to their results.
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
    if signal is not None and current_action is not None and current_action != signal.action:
        violations.append(f"current_action '{current_action}' contradicts the rule's "
                          f"'{signal.action}' ({signal.regime} regime)")
    return violations + _direction_violations(parts, signal, plans or {})
