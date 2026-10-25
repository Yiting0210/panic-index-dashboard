"""Checks the model's prose against the code-computed strategy signal.

The Market Regime and Strategy Signal panels are filled by code, so the model
only writes explanations. This validator rejects an explanation that
contradicts the panels:

* fabricated allocation: an exposure / position percentage (or "N of M
  units") that does not match the field it describes. Each figure gets a
  role from its clause (current exposure, add or reduce step, cap, floor,
  initial or previous exposure, a past level, a backtest average) and is
  compared with that field only, so a made-up current exposure cannot pass
  because it equals the add step or the cap. Figures labelled as a what-if
  are compared with the run_backtest calls the model made this turn, never
  with the signal, and other figures never with those calls;
* contradictory direction: a sentence about the current action that says
  add while the rule reduces or holds, or the reverse.

Sentences that are negated or conditional ("would add if...") are skipped by
the direction check, and levels in conditional sentences ("would take
exposure from 30% to 20%") by the position check, so describing the rule
itself is allowed. Step sizes, caps, and what-ifs are checked either way.
"""
import re
from dataclasses import dataclass

POSITION_WORDS = r"(?:exposure|position|allocation|invested)"
PCT = r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)"
# "exposure of 80%", "position is now 60 percent", "80% exposure", "60% of capital invested".
PCT_AFTER_WORD_RE = re.compile(POSITION_WORDS + r"\b([^.%\d]{0,40}?)" + PCT, re.IGNORECASE)
PCT_BEFORE_WORD_RE = re.compile(PCT + r"\s+(?:\w+\s+){0,2}?" + POSITION_WORDS + r"\b",
                                re.IGNORECASE)
UNITS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:out of|of|/)\s*(\d+)\s*units?\b", re.IGNORECASE)
# In "exposure held while the index fell 12%", the figure describes the index.
OTHER_SUBJECT_RE = re.compile(
    r"\b(?:index|prices?|returns?|returned|earned|gained|lost|drawdown|vix|volatility|market|"
    r"qqq|spx|smh|dji|nasdaq|sharpe|calmar|buy (?:&|and) hold)\b", re.IGNORECASE)
# In "90% average exposure and 35% max drawdown", the noun after the figure
# says it is not an exposure.
FIGURE_NOUN_RE = re.compile(
    r"^\s*(?:max(?:imum)?\s+|annual(?:ized)?\s+|total\s+|cumulative\s+)?"
    r"(?:returns?|drawdowns?|gains?|loss(?:es)?|declines?|rally|upside|downside|percentile|"
    r"win rate)\b", re.IGNORECASE)
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
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
# "if", but not the "-if" of "what-if" (WHAT_IF_RE handles that).
CONDITIONAL_RE = re.compile(r"\b(?:would|could|unless|will)\b|(?<!-)\bif\b", re.IGNORECASE)

# Claim roles, read from the figure's own clause.
CLAUSE_BREAK_RE = re.compile(r"[,;:()]|\b(?:and|but|while|so|which|then|whereas)\b",
                             re.IGNORECASE)
WHAT_IF_RE = re.compile(
    r"\bwhat-?if\b|\bhypothetical|\bwould (?:end|have|finish|hold|sit)\b|"
    r"\bhad (?:it|the strategy|the backtest) start", re.IGNORECASE)
# A what-if label reaches forward over commas ("In the what-if, it ends at
# 70%") but not past a contrast ("80% now, but the what-if ends at 70%").
CONTRAST_RE = re.compile(
    r";|\b(?:but|whereas|while|however|versus|vs)\b|\bcompared (?:with|to)\b", re.IGNORECASE)
# A step verb right before the figure ("added 20%", "reduced exposure by 10%");
# "reduced to 0%" states a level, not a step.
STEP_TAIL = r"(?:\s+(?:only|another|a|further|exposure|its|the|position|allocation|by))*\s*$"
# Verbs that state a step only next to a figure ("exposure rose 10%"); in a
# sentence about the current action they could describe the index, so the
# direction check does not use them.
ADD_STEP_ONLY = r"rise|rose|rises|rising|grew|grows|climbed|climbs|steps? up|stepped up"
REDUCE_STEP_ONLY = (r"remov(?:e|es|ed|ing)|fall|fell|falls|dropped|drops|steps? down|"
                    r"stepped down")
ADD_STEP_RE = re.compile(rf"\b(?:{ADD_NOW}|{ADD_PAST}|{ADD_STEP_ONLY})\b{STEP_TAIL}",
                         re.IGNORECASE)
REDUCE_STEP_RE = re.compile(rf"\b(?:{REDUCE_NOW}|{REDUCE_PAST}|{REDUCE_STEP_ONLY})\b{STEP_TAIL}",
                            re.IGNORECASE)
PER_PANIC_RE = re.compile(r"^\W*(?:\w+\s+)?(?:per|each|a)\s+panic", re.IGNORECASE)
PER_GREED_RE = re.compile(r"^\W*(?:\w+\s+)?(?:per|each|a)\s+greed", re.IGNORECASE)
# Checked in order after the step patterns; the first match wins, else "current".
ROLE_RES = (
    ("average", re.compile(r"\b(?:averag(?:e|es|ed|ing)|avg|mean)\b", re.IGNORECASE)),
    ("time_below", re.compile(r"\btime below\b|\bbelow full\b", re.IGNORECASE)),
    ("previous", re.compile(r"\b(?:previous|previously|prior|yesterday|before)\b|\bfrom\W*$",
                            re.IGNORECASE)),
    ("initial", re.compile(r"\b(?:initial|initially|began|start(?:s|ed|ing)?(?:\s+(?:at|with|from))?)\b",
                           re.IGNORECASE)),
    ("cap", re.compile(r"\b(?:cap|capped|maximum|max|ceiling|upper limit)\b", re.IGNORECASE)),
    ("floor", re.compile(r"\b(?:floor|minimum|min|lower limit)\b", re.IGNORECASE)),
    ("history", re.compile(r"\b(?:was|were|had|reached|(?:19|20)\d{2})\b", re.IGNORECASE)),
)
ROLE_LABELS = {
    "current": "the current exposure", "add": "the add step", "reduce": "the reduce step",
    "cap": "the exposure cap", "floor": "the exposure floor",
    "initial": "the initial exposure", "previous": "the previous exposure",
    "history": "a past exposure level", "average": "the average exposure",
    "time_below": "the time below full exposure",
}
# The run_backtest fields a figure with each role may match.
CALL_FIELDS = {
    "current": ("final_position_pct",),
    "add": ("add_pct",),
    "reduce": ("reduce_pct",),
    "cap": ("max_position_pct",),
    "floor": ("min_position_pct",),
    "initial": ("initial_position_pct",),
    "previous": ("previous_position_pct",),
    "history": ("final_position_pct", "previous_position_pct", "initial_position_pct",
                "max_position_pct", "min_position_pct"),
    "average": ("average_exposure_pct",),
    "time_below": ("time_below_full_exposure_pct",),
}


@dataclass(frozen=True)
class PositionClaim:
    value: float
    role: str
    what_if: bool
    sentence: str


def claim_role(before, after):
    """Role of a figure, from its clause's text before and after it."""
    clauses = CLAUSE_BREAK_RE.split(before)
    # "average exposure (59.8%)": a bracketed figure belongs to the clause
    # before the bracket.
    if before.rstrip().endswith("(") and len(clauses) > 1 and not clauses[-1].strip():
        before = clauses[-2]
    else:
        before = clauses[-1]
    after = CLAUSE_BREAK_RE.split(after, maxsplit=1)[0]
    if ADD_STEP_RE.search(before) or PER_PANIC_RE.search(after):
        return "add"
    if REDUCE_STEP_RE.search(before) or PER_GREED_RE.search(after):
        return "reduce"
    clause = f"{before} {after}"
    for role, pattern in ROLE_RES:
        if pattern.search(before if role == "previous" else clause):
            return role
    return "current"


def claim_is_what_if(before, after):
    """Whether a figure is labelled as a what-if, before it or in its own clause."""
    before = CONTRAST_RE.split(before)[-1]
    after = CLAUSE_BREAK_RE.split(CONTRAST_RE.split(after, maxsplit=1)[0], maxsplit=1)[0]
    return bool(WHAT_IF_RE.search(before) or WHAT_IF_RE.search(after))


def stated_positions(text):
    """Exposure figures stated in ``text`` with their roles (units as percent)."""
    claims = []
    for sentence in SENTENCE_RE.split(text):
        found = {}  # figure start -> (value, end), so each figure is one claim
        for m in PCT_AFTER_WORD_RE.finditer(sentence):
            # A ";" ends the exposure's clause; a figure past it, or one
            # followed by its own noun ("35% max drawdown"), is something else.
            if (";" in m.group(1) or OTHER_SUBJECT_RE.search(m.group(1))
                    or FIGURE_NOUN_RE.search(sentence[m.end():])):
                continue
            found[m.start(2)] = (float(m.group(2)), m.end(2))
        for m in PCT_BEFORE_WORD_RE.finditer(sentence):
            found.setdefault(m.start(1), (float(m.group(1)), m.end(1)))
        for m in UNITS_RE.finditer(sentence):
            if float(m.group(2)) > 0:
                found[m.start(1)] = (float(m.group(1)) / float(m.group(2)) * 100, m.end())
        for start, (value, end) in sorted(found.items()):
            before, after = sentence[:start], sentence[end:]
            claims.append(PositionClaim(value, claim_role(before, after),
                                        claim_is_what_if(before, after), sentence.strip()))
    return claims


def allowed_positions(signal, tool_calls=(), role="current", what_if=False):
    """Values a figure with this role may state.

    A what-if figure may match only the run_backtest calls made this turn and
    any other figure only the standard backtest's signal. Averages always come
    from run_backtest calls, since the signal does not carry them.

    TODO: what-if figures are pooled across all of this turn's run_backtest
    calls, so with two scenarios a figure from scenario B passes when stated
    for scenario A. Separating them needs each claim tied to its call's
    parameters (threshold, start date, steps); see
    test_what_if_scenarios_are_not_separated_yet.
    """
    if what_if or role in ("average", "time_below"):
        allowed = set()
        for call in tool_calls:
            if call.name == "run_backtest" and call.result and not call.is_error:
                allowed.update(float(call.result[f]) for f in CALL_FIELDS[role]
                               if call.result.get(f) is not None)
                change = call.result.get("latest_position_change_pct")
                if change and role == ("add" if change > 0 else "reduce"):
                    allowed.add(abs(float(change)))
        return allowed
    # The step actually applied, smaller than add_pct / reduce_pct when clipped.
    change = signal.final_position_pct - signal.previous_position_pct
    return {
        "current": {signal.final_position_pct},
        "add": {signal.add_pct} | ({change} if change > 0 else set()),
        "reduce": {signal.reduce_pct} | ({-change} if change < 0 else set()),
        "cap": {signal.max_position_pct},
        "floor": {signal.min_position_pct},
        "initial": {signal.initial_position_pct},
        "previous": {signal.previous_position_pct},
        "history": {signal.final_position_pct, signal.previous_position_pct,
                    signal.initial_position_pct, signal.max_position_pct,
                    signal.min_position_pct},
    }[role]


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


# Roles the model may declare in position_claims, and the validator role each maps to.
CLAIM_ROLES = {
    "current": "current", "previous": "previous", "add_step": "add",
    "reduce_step": "reduce", "cap": "cap", "floor": "floor", "initial": "initial",
    "average": "average", "time_below_full": "time_below", "past_level": "history",
}
ACTIONS = ("add", "reduce", "hold")


def grounded_numbers(signal, tool_calls=()):
    """Every number in the signal and this turn's tool results."""
    found = []

    def walk(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            found.append(float(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)

    walk(vars(signal))
    for call in tool_calls:
        if call.result and not call.is_error:
            walk(call.result)
    return found


def _near(value, candidates, tolerance):
    return any(abs(value - ok) <= tolerance for ok in candidates)


def _violation(value, role, what_if, allowed, sentence, verb):
    expected = (", ".join(f"{ok:g}%" for ok in sorted(allowed)) if allowed
                else "a run_backtest call this turn")
    label = ROLE_LABELS[role] + (" in a what-if" if what_if else "")
    return f"fabricated position {value:g}% ({verb} {label}; expected {expected}): {sentence}"


def check_declared(claims, current_action, signal, tool_calls=(), tolerance=0.51):
    """Violations among the figures and action the model declared itself.

    The model lists each exposure figure it states with its role, so no
    prose parsing is needed: each figure is checked against its own field.
    """
    violations = []
    for claim in claims:
        role = CLAIM_ROLES[claim["role"]]
        allowed = allowed_positions(signal, tool_calls, role, claim["what_if"])
        if not _near(claim["value"], allowed, tolerance):
            violations.append(_violation(claim["value"], role, claim["what_if"], allowed,
                                         "position_claims", "declared as"))
    if current_action in ACTIONS and current_action != signal.action:
        violations.append(f"current_action '{current_action}' contradicts the rule's "
                          f"'{signal.action}' ({signal.regime} regime)")
    return violations


def validate_answer(text, signal, tool_calls=(), tolerance=0.51, claims=None,
                    current_action=None):
    """Violations of the strategy signal in ``text``; empty when consistent.

    Without ``claims`` the prose is parsed for exposure figures and each is
    checked against the field its clause suggests. With ``claims`` (the
    model's declared figures) those are checked by role, and a figure the
    parser finds in the prose but the model did not declare fails only when
    it matches nothing in the signal or this turn's tool results, so a
    return or drawdown next to the word "exposure" is not misread.
    """
    structured = claims is not None
    violations = (check_declared(claims, current_action, signal, tool_calls, tolerance)
                  if structured else [])
    declared = [c["value"] for c in claims or ()]
    grounded = grounded_numbers(signal, tool_calls) if structured else []
    seen = set()
    for claim in stated_positions(text):
        if (claim.value, claim.role, claim.what_if) in seen:
            continue
        seen.add((claim.value, claim.role, claim.what_if))
        if structured and _near(claim.value, declared, tolerance):
            continue
        # A level in a conditional sentence ("another close below 15 would take
        # exposure from 30% to 20%") is a hypothetical path, not a claim about
        # the current or past position; steps, caps, and what-ifs are still checked.
        if (not claim.what_if and claim.role in ("current", "previous", "history")
                and CONDITIONAL_RE.search(claim.sentence)):
            continue
        allowed = allowed_positions(signal, tool_calls, claim.role, claim.what_if)
        if _near(claim.value, allowed, tolerance):
            continue
        if structured and _near(claim.value, grounded, tolerance):
            continue
        violations.append(_violation(claim.value, claim.role, claim.what_if, allowed,
                                     claim.sentence, "read as"))
    for action, sentence in stated_actions(text, present_only=signal.action == "hold"):
        if action != signal.action:
            violations.append(
                f"direction '{action}' contradicts the rule's '{signal.action}' "
                f"({signal.regime} regime): {sentence}"
            )
    return violations
