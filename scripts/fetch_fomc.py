"""Download FOMC statements, minutes, and Chair press conferences.

Each document is stored with the date it became public (``published_at``),
not the meeting date. Minutes are released roughly three weeks after a
meeting, so indexing them by meeting date would leak information into any
as-of query that falls between the meeting and the release. Press
conferences are broadcast live on the decision day and take that date, even
though the transcript PDF is often posted days or weeks later.

Usage:
    python scripts/fetch_fomc.py --start-year 2021            # full rebuild
    python scripts/fetch_fomc.py --incremental                # new documents only
"""
import argparse
import json
import re
import time
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "fomc" / "documents.jsonl"
HEADERS = {"User-Agent": "panic-index-dashboard research corpus builder"}

STATEMENT_RE = re.compile(r"/newsevents/pressreleases/monetary(\d{8})a\.htm$")
MINUTES_RE = re.compile(r"/monetarypolicy/fomcminutes(\d{8})\.htm$")
PRESCONF_RE = re.compile(r"/monetarypolicy/fomcpresconf(\d{8})\.htm$")
TRANSCRIPT_PDF_RE = re.compile(r"/FOMCpresconf\d{8}\.pdf$", re.IGNORECASE)
RELEASED_RE = re.compile(r"Released\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})")
# Non-breaking hyphens and en/em dashes appear in rate ranges such as "1‑1/2".
DASHES = str.maketrans({"‐": "-", "‑": "-", "–": "-", "—": "-"})
# Transcripts write rates as "3½"; statements write "3-1/2".
FRACTIONS = {"¼": "1/4", "½": "1/2", "¾": "3/4"}
FRACTION_RE = re.compile(r"(\d?)([¼½¾])")
TRANSCRIPT_HEADING_RE = re.compile(
    r"Transcript of (Chair(?:man|woman)?) ([A-Z][A-Za-z-]+)['’]s Press Conference"
)
# Running header and page number repeated on every transcript page.
RUNNING_HEAD_RE = re.compile(r"^[A-Z][a-z]+ \d{1,2}, \d{4}\s+.*Press Conference\s+FINAL$")
PAGE_NUMBER_RE = re.compile(r"^Page \d+ of \d+$")
# A speaker turn opens a line with an upper-case name: "CHAIR POWELL.  Good afternoon."
TURN_RE = re.compile(r"^((?:[A-Z][A-Z'-]*\.? )+[A-Z][A-Z'-]+)\.\s+(.*)$")


class TranscriptPending(Exception):
    """The press conference page does not link a transcript PDF yet."""


def _parse_ymd(value):
    return datetime.strptime(value, "%Y%m%d").date()


def parse_calendar(html):
    """Return statement, minutes, and press conference entries on the FOMC calendar."""
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
            presconf_match = PRESCONF_RE.search(href)
            if presconf_match:
                held = _parse_ymd(presconf_match.group(1))
                entries.append({
                    "doc_type": "press_conference",
                    "meeting_date": held,
                    "published_at": held,
                    # A few calendar links are absolute.
                    "url": urljoin(BASE_URL, href),
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


def transcript_pdf_url(html):
    """URL of the transcript PDF linked from a press conference page."""
    soup = BeautifulSoup(html, "html.parser")
    for link in soup.select("#article a[href]"):
        if TRANSCRIPT_PDF_RE.search(link["href"]):
            return urljoin(BASE_URL, link["href"])
    raise TranscriptPending("transcript PDF not linked yet")


def _normalize_fractions(text):
    return FRACTION_RE.sub(
        lambda m: f"{m.group(1)}-{FRACTIONS[m.group(2)]}" if m.group(1)
        else FRACTIONS[m.group(2)],
        text,
    )


def pdf_lines(content):
    reader = PdfReader(BytesIO(content))
    return [line for page in reader.pages for line in page.extract_text().splitlines()]


def extract_transcript(lines):
    """Return ``(role, speaker, text)`` from press conference transcript lines.

    ``text`` holds one speaker turn per paragraph ("CHAIR POWELL. ..."), with
    running headers and page numbers removed and wrapped lines rejoined.
    """
    role = speaker = None
    turns = []
    for line in lines:
        line = line.strip()
        if not line or RUNNING_HEAD_RE.match(line) or PAGE_NUMBER_RE.match(line):
            continue
        heading = TRANSCRIPT_HEADING_RE.search(line)
        if heading and not turns:
            role, speaker = heading.groups()
            continue
        turn = TURN_RE.match(line)
        if turn:
            turns.append(f"{turn.group(1)}. {turn.group(2)}")
        elif turns:
            # A line ending in a hyphen or dash continues the same word.
            joiner = "" if turns[-1].endswith(("-", "‑", "–", "—")) else " "
            turns[-1] = f"{turns[-1]}{joiner}{line}"
    if speaker is None or not turns:
        raise ValueError("transcript heading or speaker turns not found")
    text = "\n\n".join(turns).translate(DASHES).replace("\xa0", " ")
    text = _normalize_fractions(text)
    return role, speaker, re.sub(r"[ \t]+", " ", text).strip()


def page_heading(html):
    heading = BeautifulSoup(html, "html.parser").select_one("#article h3.title")
    return heading.get_text(" ", strip=True) if heading else ""


def build_title(entry, heading=""):
    meeting = entry["meeting_date"].strftime("%B %d, %Y")
    if entry["doc_type"] == "press_conference":
        # heading is "Chair Powell" or "Chairman Warsh".
        return f"{heading}'s Press Conference ({meeting})"
    if entry["doc_type"] == "statement":
        if heading and "FOMC statement" not in heading:
            # e.g. the August 2025 revision of the Longer-Run Goals statement.
            return f"{heading} ({meeting})"
        return f"FOMC Statement ({meeting})"
    return f"Minutes of the FOMC Meeting ({meeting})"


def fetch_transcript_record(session, entry, html):
    response = session.get(transcript_pdf_url(html), timeout=60)
    response.raise_for_status()
    role, speaker, text = extract_transcript(pdf_lines(response.content))
    return {
        "doc_id": f"press_conference-{entry['meeting_date']:%Y%m%d}",
        "doc_type": "press_conference",
        "title": build_title(entry, f"{role} {speaker}"),
        "meeting_date": entry["meeting_date"].isoformat(),
        "published_at": entry["published_at"].isoformat(),
        "url": entry["url"],
        "speaker": speaker,
        "text": text,
    }


def fetch_record(session, entry):
    response = session.get(entry["url"], timeout=30)
    response.raise_for_status()
    # The server sends no charset; the pages are UTF-8.
    html = response.content.decode("utf-8")
    if entry["doc_type"] == "press_conference":
        return fetch_transcript_record(session, entry, html)
    return {
        "doc_id": f"{entry['doc_type']}-{entry['meeting_date']:%Y%m%d}",
        "doc_type": entry["doc_type"],
        "title": build_title(entry, page_heading(html)),
        "meeting_date": entry["meeting_date"].isoformat(),
        "published_at": entry["published_at"].isoformat(),
        "url": entry["url"],
        "text": extract_text(html, entry["doc_type"]),
    }


def load_existing(path=OUTPUT_PATH):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def write_documents(records, path=OUTPUT_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda r: (r["published_at"], r["doc_type"]))
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def update(start_year, end_year, incremental=False, sleep=0.5):
    """Fetch documents; with ``incremental`` only URLs not already stored.

    Stored documents are never re-downloaded or rewritten in incremental
    mode, matching the append-only policy for market data. A press
    conference whose transcript is not posted yet is skipped rather than
    failing the run, so a later run picks it up. Returns the new records.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    calendar = session.get(CALENDAR_URL, timeout=30)
    calendar.raise_for_status()
    entries = [
        e for e in parse_calendar(calendar.text)
        if start_year <= e["meeting_date"].year <= end_year
    ]
    existing = load_existing() if incremental else []
    known_urls = {r["url"] for r in existing}

    new_records = []
    for entry in entries:
        if entry["url"] in known_urls:
            continue
        try:
            record = fetch_record(session, entry)
        except TranscriptPending:
            print(f"press_conference-{entry['meeting_date']:%Y%m%d}: transcript not posted yet")
            continue
        new_records.append(record)
        print(f"{record['doc_id']:<30} published {record['published_at']}  "
              f"{len(record['text']):>6} chars")
        time.sleep(sleep)
    if new_records or not incremental:
        write_documents(existing + new_records)
    print(f"{len(new_records)} new documents; {len(existing) + len(new_records)} total in {OUTPUT_PATH}")
    return new_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2021)
    parser.add_argument("--end-year", type=int, default=date.today().year)
    parser.add_argument("--incremental", action="store_true",
                        help="Only download documents not already in documents.jsonl")
    parser.add_argument("--sleep", type=float, default=0.5,
                        help="Seconds between requests to federalreserve.gov")
    args = parser.parse_args()
    update(args.start_year, args.end_year, args.incremental, args.sleep)


if __name__ == "__main__":
    main()
