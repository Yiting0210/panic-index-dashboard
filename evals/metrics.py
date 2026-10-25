"""Metric functions shared by the retrieval, agent, and point-in-time evals."""
import re
from datetime import date

# Unit suffixes such as "pp" or "bp" are allowed; (?!\.\d) stops "-83.87pp"
# from backtracking to "-83".
NUMBER_RE = re.compile(
    r"(?<![\w/])(-?\d+(?:,\d{3})*(?:\.\d+)?)(?:st|nd|rd|th|pp|bps?|x)?(?![\w/])(?!\.\d)"
)
ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Short dates without a year, e.g. "03-07" or "06-15/06-21".
MONTH_DAY = r"(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
MONTH_DAY_RE = re.compile(rf"(?<![\d-]){MONTH_DAY}(?:/{MONTH_DAY})?(?![\d-])")
MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(?:\d{1,2},\s+)?(\d{4})\b"
)
# Prose dates such as "April 9", "Apr 11, 2025", "September 18th", or a
# two-day meeting such as "March 15–16".
PROSE_DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?(?:\s*[-–]\s*\d{1,2})?(?:,\s*\d{4})?\b"
)
MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October "
    "November December".split(), start=1)}


def unique_doc_ids(hits):
    seen = []
    for hit in hits:
        doc_id = hit["doc_id"] if isinstance(hit, dict) else hit.doc_id
        if doc_id not in seen:
            seen.append(doc_id)
    return seen


def _answer_groups(relevant):
    """Each label is a doc id or a list of alternatives that answer equally well."""
    return [{item} if isinstance(item, str) else set(item) for item in relevant]


def recall_at_k(retrieved_doc_ids, relevant, k):
    if not relevant:
        return float("nan")
    top = set(retrieved_doc_ids[:k])
    groups = _answer_groups(relevant)
    return sum(bool(top & group) for group in groups) / len(groups)


def reciprocal_rank(retrieved_doc_ids, relevant):
    accepted = set().union(*_answer_groups(relevant))
    for rank, doc_id in enumerate(retrieved_doc_ids, start=1):
        if doc_id in accepted:
            return 1.0 / rank
    return 0.0


def leaked_hits(hits, as_of):
    """Hits whose release date is after the as-of cutoff (should be empty)."""
    leaked = []
    for hit in hits:
        published = hit["published_at"] if isinstance(hit, dict) else hit.published_at
        if isinstance(published, str):
            published = date.fromisoformat(published)
        if published > as_of:
            leaked.append(hit)
    return leaked


def extract_numbers(text):
    """Numbers stated in free text, ignoring years and dates.

    Dates are checked separately (future_dates); stripping them here keeps a
    day of the month from counting as an unsupported number when the tool
    reported the same day in ISO form.
    """
    text = MONTH_DAY_RE.sub(" ", PROSE_DATE_RE.sub(" ", ISO_DATE_RE.sub(" ", text)))
    values = []
    for match in NUMBER_RE.findall(text):
        value = float(match.replace(",", ""))
        if value.is_integer() and 1900 <= value <= 2100 and "." not in match:
            continue
        values.append(value)
    return values


def collect_numbers(payload):
    """All numeric leaves in a (nested) tool result."""
    found = []
    if isinstance(payload, bool):
        return found
    if isinstance(payload, (int, float)):
        found.append(float(payload))
    elif isinstance(payload, dict):
        for value in payload.values():
            found.extend(collect_numbers(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            found.extend(collect_numbers(value))
    elif isinstance(payload, str):
        found.extend(extract_numbers(payload))
    return found


def numeric_faithfulness(answer_numbers, source_numbers, rel_tol=0.01, abs_tol=0.051):
    """Share of numbers in the answer that match some tool or source number.

    Matching allows rounding (abs 0.05) and small relative error, so "26.7%"
    matches a tool value of 26.69. Returns (score, unsupported_numbers).
    """
    if not answer_numbers:
        return 1.0, []
    unsupported = [
        value for value in answer_numbers
        if not any(
            abs(value - source) <= max(abs_tol, rel_tol * abs(source))
            for source in source_numbers
        )
    ]
    return 1 - len(unsupported) / len(answer_numbers), unsupported


def dates_mentioned(text):
    """Dates stated in the answer, at day or month granularity."""
    found = []
    for year, month, day in ISO_DATE_RE.findall(text):
        found.append(date(int(year), int(month), int(day)))
    for month, year in MONTH_YEAR_RE.findall(text):
        found.append(date(int(year), MONTHS[month], 1))
    return found


def future_dates(text, as_of):
    """Dates in the answer that fall after the as-of cutoff."""
    return [d for d in dates_mentioned(text) if d > as_of]
