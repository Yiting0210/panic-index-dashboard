"""Lightweight query understanding for dated FOMC questions."""
import re

MONTHS = {
    m: i for i, m in enumerate(
        "january february march april may june july august september "
        "october november december".split(), start=1)
}
MONTH_YEAR_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")\s+(?:\d{1,2},\s*)?(20\d{2})\b", re.IGNORECASE
)


def month_mentions(query):
    """(year, month) pairs named in the query, e.g. "June 2022" -> (2022, 6)."""
    return sorted({
        (int(year), MONTHS[month.lower()])
        for month, year in MONTH_YEAR_RE.findall(query)
    })
