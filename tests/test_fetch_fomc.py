"""FOMC corpus builder: calendar parsing and press conference transcripts."""
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

import fetch_fomc  # noqa: E402

CALENDAR = """
<div class="fomc-meeting">
  <a href="/newsevents/pressreleases/monetary20220316a.htm">HTML</a>
  <a href="https://www.federalreserve.gov/monetarypolicy/fomcpresconf20220316.htm">Press Conference</a>
  <a href="/monetarypolicy/fomcminutes20220316.htm">HTML</a>
  (Released April 06, 2022)
</div>
"""

TRANSCRIPT_LINES = [
    "March 16, 2022   Chair Powell’s Press Conference  FINAL ",
    " ",
    "Page 1 of 3 ",
    "Transcript of Chair Powell’s Press Conference ",
    "March 16, 2022 ",
    "CHAIR POWELL.  Good afternoon.  Today the FOMC raised its policy rate by ¼ ",
    "percentage point, to a range of ¼ to ½ percent.  We expect a longer-",
    "run path ",
    "March 16, 2022   Chair Powell’s Press Conference  FINAL ",
    "Page 2 of 3 ",
    "that is appropriate.  The U.S. economy is strong. ",
    "MICHELLE SMITH.  Thank you.  Steve. ",
    "STEVE LIESMAN.  Mr. Chair, how many hikes do you expect? ",
    "CHAIR POWELL.  We will be nimble. ",
]


def test_calendar_lists_press_conferences_on_the_decision_day():
    entries = fetch_fomc.parse_calendar(CALENDAR)
    presconf = [e for e in entries if e["doc_type"] == "press_conference"]
    assert presconf == [{
        "doc_type": "press_conference",
        "meeting_date": date(2022, 3, 16),
        "published_at": date(2022, 3, 16),
        "url": "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20220316.htm",
    }]
    assert {e["doc_type"] for e in entries} == {"statement", "minutes", "press_conference"}


def test_calendar_accepts_the_pressconf_spelling():
    calendar = """
    <div class="fomc-meeting">
      <a href="/newsevents/pressreleases/monetary20260128a.htm">HTML</a>
      <a href="/monetarypolicy/fomcpressconf20260128.htm">Press Conference</a>
    </div>
    """
    presconf = [e for e in fetch_fomc.parse_calendar(calendar)
                if e["doc_type"] == "press_conference"]
    assert presconf == [{
        "doc_type": "press_conference",
        "meeting_date": date(2026, 1, 28),
        "published_at": date(2026, 1, 28),
        "url": "https://www.federalreserve.gov/monetarypolicy/fomcpressconf20260128.htm",
    }]


def test_extract_transcript_joins_turns_and_drops_page_furniture():
    role, speaker, text = fetch_fomc.extract_transcript(TRANSCRIPT_LINES)
    assert (role, speaker) == ("Chair", "Powell")
    turns = text.split("\n\n")
    assert [t.split(". ", 1)[0] for t in turns] == [
        "CHAIR POWELL", "MICHELLE SMITH", "STEVE LIESMAN", "CHAIR POWELL"
    ]
    opening = turns[0]
    assert "FINAL" not in text and "Page " not in text
    # Fractions match the statements' "1/4" style; wrapped hyphenation is rejoined.
    assert "by 1/4 percentage point, to a range of 1/4 to 1/2 percent" in opening
    assert "longer-run path that is appropriate" in opening
    # "U.S." at the start of a wrapped line is not mistaken for a speaker.
    assert "The U.S. economy is strong." in opening


def test_extract_transcript_reads_the_chairman_title():
    lines = ["Transcript of Chairman Warsh’s Press Conference", "CHAIRMAN WARSH.  Good day."]
    assert fetch_fomc.extract_transcript(lines) == ("Chairman", "Warsh", "CHAIRMAN WARSH. Good day.")


def test_extract_transcript_rejects_unrecognised_text():
    with pytest.raises(ValueError):
        fetch_fomc.extract_transcript(["Some other document"])


def test_missing_transcript_pdf_is_pending_not_an_error():
    page = '<div id="article"><a href="/monetarypolicy/files/fomcminutes20260916.pdf">Minutes</a></div>'
    with pytest.raises(fetch_fomc.TranscriptPending):
        fetch_fomc.transcript_pdf_url(page)
    page = '<div id="article"><a href="/mediacenter/files/FOMCpresconf20260729.pdf">PDF</a></div>'
    assert fetch_fomc.transcript_pdf_url(page) == (
        "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260729.pdf"
    )


def test_build_title_for_press_conference():
    entry = {"doc_type": "press_conference", "meeting_date": date(2026, 7, 29)}
    assert fetch_fomc.build_title(entry, "Chairman Warsh") == (
        "Chairman Warsh's Press Conference (July 29, 2026)"
    )
