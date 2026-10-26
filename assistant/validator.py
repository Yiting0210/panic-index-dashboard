"""Runtime contract checks on the model's explanation.

Critical decision facts (strategy path exposure, the user's position, the
rule's action, the applied change, the resulting position) are code-owned:
assistant/presentation.py builds them, the page renders them, and the model
gets them as read-only context. So this module does not try to understand
what the prose means. It blocks only what a machine can check:

* placeholder errors: an unknown placeholder, call id, or field
  (assistant/render.py);
* raw position figure: a percentage the model wrote itself (not produced by
  a placeholder) in the same clause as a position word (exposure, position,
  allocation, invested), or any "N of M units". Returns, drawdowns, and
  index levels are fine in their own clause. This is the one rule against
  the model restating a decision fact; it is a format rule, not language
  understanding, and wording outside it ("the rule holds 100%") is not
  covered;
* citations: every cited doc_id must come from this run's search_fed_docs
  results and be published on or before as_of.

Whether the prose contradicts the decision or is unsupported is measured in
the evals (evals/judge.py), not blocked here.
"""
import re
from datetime import date

from .render import Rendered

POSITION_WORD_RE = re.compile(
    r"\b(?:exposures?|positions?|allocations?|invested|units?)\b", re.IGNORECASE)
PCT_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE)
UNITS_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:out of|of|/)\s*\d+\s*units?\b", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")
CLAUSE_BREAK_RE = re.compile(
    r"[,;:()]|\b(?:and|but|while|so|which|then|whereas|versus|vs\.?|compared)\b",
    re.IGNORECASE)


def _segments(text, pattern):
    """(start, end) of the pieces of ``text`` between matches of ``pattern``."""
    start = 0
    for m in pattern.finditer(text):
        yield start, m.start()
        start = m.end()
    yield start, len(text)


def raw_position_figures(rendered):
    """Position figures the model wrote itself instead of leaving them to code."""
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


def citation_violations(citations, retrieved, as_of):
    """Cited doc_ids this run did not retrieve, or that were published after as_of.

    ``retrieved`` maps doc_id -> published_at for every search_fed_docs hit.
    """
    violations = []
    for citation in citations or []:
        doc_id = citation.get("doc_id")
        if doc_id not in retrieved:
            violations.append(f"citation {doc_id} was not returned by search_fed_docs "
                              "in this run; cite only returned doc_ids")
        elif as_of is not None and date.fromisoformat(str(retrieved[doc_id])) > as_of:
            violations.append(f"citation {doc_id} was published after as_of {as_of}")
    return violations


def validate_answer(rendered, citations=None, retrieved=None, as_of=None):
    """Contract violations; empty when the explanation keeps the contract.

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
                f"raw position figure {figure}: position and action facts are shown by "
                f"the app; do not restate them (what-if figures use {{btN...}} "
                f"placeholders): {sentence}")
    return violations + citation_violations(citations, retrieved or {}, as_of)
