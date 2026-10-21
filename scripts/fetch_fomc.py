"""Download FOMC policy statements and minutes into a point-in-time corpus.

Each document is stored with the date it became public (``published_at``),
not the meeting date. Minutes are released roughly three weeks after a
meeting, so indexing them by meeting date would leak information into any
as-of query that falls between the meeting and the release.

Usage:
    python scripts/fetch_fomc.py --start-year 2021 --end-year 2026
"""
import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "fomc" / "documents.jsonl"
HEADERS = {"User-Agent": "panic-index-dashboard research corpus builder"}

STATEMENT_RE = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm$")
MINUTES_RE = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm$")
RELEASED_RE = re.compile(r"Released\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
# Non-breaking hyphens and en/em dashes appear in rate ranges such as "1‑1/2".
DASHES = str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-"})


def _parse_ymd(value):
    return datetime.strptime(value, "%Y%m%d").date()


def parse_calendar(html):
    """Return statement and minutes entries listed on the FOMC calendar."""
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for meeting in soup.select("div.fomc-meeting"):
        statement_date = None
        for link in meeting.find_all("a", href=True):
            href = link["href"]
            statement_match = STATEMENT_RE.search(href)
            if statement_match:
                statement_date = _parse_ymd(statement_match.group(1))
                entries.append({
                    "doc_type": "statement",
                    "meeting_date": statement_date,
                    "published_at": statement_date,
                    "url": BASE_URL + href,
                })
            minutes_match = MINUTES_RE.search(href)
            if minutes_match:
                released = RELEASED_RE.search(meeting.get_text(" ", strip=True))
                if not released:
                    continue
                entries.append({
                    "doc_type": "minutes",
                    "meeting_date": _parse_ymd(minutes_match.group(1)),
                    "published_at": datetime.strptime(
                        released.group(1), "%B %d, %Y"
                    ).date(),
                    "url": BASE_URL + href,
                })
    unique = {entry["url"]: entry for entry in entries}
    return sorted(unique.values(), key=lambda e: (e["published_at"], e["doc_type"]))


def extract_text(html, doc_type):
    """Extract readable body text from a statement or minutes page."""
    soup = BeautifulSoup(html, "html.parser")
    article = soup.find("div", id="article")
    if article is None:
        raise ValueError("article container not found")
    if doc_type == "statement":
        bodies = article.find_all("div", class_="col-sm-8", recursive=False)
        container = bodies[-1] if bodies else article
    else:
        container = article
    for tag in container.find_all(["script", "style", "ul"]):
        tag.decompose()
    paragraphs = [
        p.get_text(" ", strip=True)
        for p in container.find_all(["p", "h4", "h5"])
    ]
    paragraphs = [
        p for p in paragraphs
        if p
        and not p.startswith(("Last Update", "For media inquiries"))
        and not p.endswith("Return to text")
    ]
    text = "\n\n".join(paragraphs)
    # Drop the voting / implementation boilerplate links at the end of statements.
    text = re.split(r"\n\nImplementation Note issued", text)[0]
    text = text.translate(DASHES).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def page_heading(html):
    heading = BeautifulSoup(html, "html.parser").select_one("#article h3.title")
    return heading.get_text(" ", strip=True) if heading else ""


def build_title(entry, heading=""):
    meeting = entry["meeting_date"].strftime("%B %d, %Y")
    if entry["doc_type"] == "statement":
        if heading and "FOMC statement" not in heading:
            # e.g. the August 2025 revision of the Longer-Run Goals statement.
            return f"{heading} ({meeting})"
        return f"FOMC Statement ({meeting})"
    return f"Minutes of the FOMC Meeting ({meeting})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--sleep", type=float, default=0.5,
                        help="Seconds between requests to federalreserve.gov")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(HEADERS)
    calendar = session.get(CALENDAR_URL, timeout=30)
    calendar.raise_for_status()
    entries = [
        e for e in parse_calendar(calendar.text)
        if args.start_year <= e["meeting_date"].year <= args.end_year
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        for entry in entries:
            response = session.get(entry["url"], timeout=30)
            response.raise_for_status()
            # The server sends no charset; the pages are UTF-8.
            html = response.content.decode("utf-8")
            text = extract_text(html, entry["doc_type"])
            doc_id = f"{entry['doc_type']}-{entry['meeting_date']:%Y%m%d}"
            record = {
                "doc_id": doc_id,
                "doc_type": entry["doc_type"],
                "title": build_title(entry, page_heading(html)),
                "meeting_date": entry["meeting_date"].isoformat(),
                "published_at": entry["published_at"].isoformat(),
                "url": entry["url"],
                "text": text,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1
            print(f"{doc_id:<22} published {record['published_at']}  {len(text):>6} chars")
            time.sleep(args.sleep)
    print(f"Wrote {written} documents to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
