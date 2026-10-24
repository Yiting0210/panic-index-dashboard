"""Append-only merge of freshly fetched market data into the CSV.

Rules:

* **Never overwrite.** A value already in the file is kept even if the
  source now reports something else; the difference is logged as an
  upstream revision. (CNN revises its own Fear & Greed history.)
* **Fill gaps, append new dates.** Empty cells on existing dates are
  filled, and trading days not yet in the file are appended with
  ``created_at`` set to the ingestion time. Weekends and market holidays
  are appended with the Fear & Greed reading only (prices blank) when the
  source has one.
* **Wait for complete sessions.** A trading day missing any close or the
  Fear & Greed score is not appended (never filled from the previous day)
  and is retried on the next run, with a warning naming what is missing.
* **Only closed sessions.** Dates whose New York session has not closed
  yet are dropped, because both sources report live intraday values.
* **Chain-link adjusted prices.** Yahoo re-bases dividend-adjusted closes
  after every ex-dividend date, so fetched ETF prices are rescaled to match
  the last complete row already stored. Returns across the seam are exact
  and stored history is untouched.
* **Validate before writing.** Any quality failure aborts the whole batch.
"""
import csv
import io
import math
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .trading_calendar import is_trading_day

PRICE_COLUMNS = ["qqq_close", "smh_close", "spx_close", "dji_close"]
VALUE_COLUMNS = PRICE_COLUMNS + ["vix", "fear_greed_index", "fear_greed_rating"]
CHAIN_LINKED = ["qqq_close", "smh_close"]
RATINGS = {"extreme fear", "fear", "neutral", "greed", "extreme greed"}
DECIMALS = {"qqq_close": 2, "smh_close": 2, "spx_close": 2, "dji_close": 2,
            "vix": 2, "fear_greed_index": 4}

NEW_YORK = ZoneInfo("America/New_York")
SESSION_SETTLED = time(16, 30)
MAX_EQUITY_MOVE = 0.20       # |daily return| above this is treated as bad data
MAX_VIX_RATIO = 4.0          # VIX can double in a day; 4x is a data error
MAX_LINK_ADJUSTMENT = 0.05   # chain-link factors beyond +/-5% suggest a split or bad print
REVISION_TOLERANCE = {"fear_greed_index": 0.5, "price": 0.005}


class DataQualityError(ValueError):
    pass


@dataclass
class UpdateReport:
    anchor_date: date
    last_settled_session: date
    appended_dates: list = field(default_factory=list)
    filled_cells: int = 0
    link_factors: dict = field(default_factory=dict)
    revisions_ignored: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(self.appended_dates or self.filled_cells)

    def as_dict(self):
        return {
            "anchor_date": self.anchor_date.isoformat(),
            "last_settled_session": self.last_settled_session.isoformat(),
            "appended_rows": len(self.appended_dates),
            "first_appended": self.appended_dates[0].isoformat() if self.appended_dates else None,
            "last_appended": self.appended_dates[-1].isoformat() if self.appended_dates else None,
            "filled_cells": self.filled_cells,
            "link_factors": {k: round(v, 6) for k, v in self.link_factors.items()},
            "revisions_ignored": len(self.revisions_ignored),
            "revision_examples": self.revisions_ignored[:10],
            "warnings": self.warnings,
        }


def last_settled_session(now=None):
    """Latest calendar date whose New York trading session has closed."""
    now = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    if now.time() >= SESSION_SETTLED:
        return now.date()
    return now.date() - timedelta(days=1)


def format_date(day):
    return f"{day.day}/{day.month}/{day.year} 00:00:00"


def format_timestamp(moment):
    return f"{moment.day}/{moment.month}/{moment.year} {moment:%H:%M:%S}"


def _format_value(column, value):
    if column == "fear_greed_rating":
        return str(value)
    return f"{round(float(value), DECIMALS[column]):.{DECIMALS[column]}f}".rstrip("0").rstrip(".")


def _is_blank(cell):
    return cell is None or (isinstance(cell, str) and cell.strip() == "")


def read_raw(path):
    """Read the CSV as strings so untouched cells are written back verbatim."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        return reader.fieldnames, list(reader)


def write_raw(path, fieldnames, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
        fh.write(buffer.getvalue())
    os.replace(tmp, path)


def _row_date(row):
    return pd.to_datetime(row["date"], dayfirst=True).date()


def _numeric(rows):
    """Numeric view of the raw rows, indexed by date (sorted)."""
    frame = pd.DataFrame(rows)
    frame.index = [_row_date(r) for r in rows]
    for column in PRICE_COLUMNS + ["vix", "fear_greed_index"]:
        # Blank cells become NaN; never Series.replace("", None), which
        # forward-fills on pandas < 3.
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_index()


def last_complete_date(rows):
    """Last stored date with every price column present."""
    complete = _numeric(rows).dropna(subset=PRICE_COLUMNS + ["vix"])
    if complete.empty:
        raise DataQualityError("no complete price row to anchor the update")
    return complete.index[-1]


def anchor_date(rows, trading_days):
    """Chain-link anchor: the last complete stored row that is a real session.

    Weekend snapshot rows can repeat Friday's prices; they are skipped
    because the source has no close for them.
    """
    complete = _numeric(rows).dropna(subset=PRICE_COLUMNS + ["vix"])
    candidates = [d for d in complete.index if d in set(trading_days)]
    if not candidates:
        raise DataQualityError("no stored trading day overlaps the fetched window")
    return candidates[-1]


def link_factors(existing, fetched, anchor):
    """Scale factors that make fetched adjusted closes continuous with the file."""
    factors = {}
    for column in CHAIN_LINKED:
        if anchor not in fetched.index or pd.isna(fetched.at[anchor, column]):
            raise DataQualityError(f"source has no {column} on anchor date {anchor}")
        factor = existing.at[anchor, column] / fetched.at[anchor, column]
        if abs(factor - 1) > MAX_LINK_ADJUSTMENT:
            raise DataQualityError(
                f"{column} chain-link factor {factor:.4f} on {anchor} exceeds "
                f"{MAX_LINK_ADJUSTMENT:.0%}; check for a split or bad print"
            )
        factors[column] = factor
    return factors


def merge(rows, fetched, now=None):
    """Return (new_rows, report). ``fetched`` is indexed by date with VALUE_COLUMNS."""
    now = now or datetime.now(NEW_YORK)
    settled = last_settled_session(now)
    fetched = fetched.copy()
    fetched.index = pd.Index([pd.Timestamp(d).date() for d in fetched.index])
    trading_days = fetched.dropna(subset=CHAIN_LINKED).index
    anchor = anchor_date(rows, trading_days)
    report = UpdateReport(anchor_date=anchor, last_settled_session=settled)

    existing = _numeric(rows)
    report.link_factors = link_factors(existing, fetched, anchor)
    for column, factor in report.link_factors.items():
        fetched[column] = fetched[column] * factor
    fetched = fetched[fetched.index <= settled]

    rows = [dict(r) for r in rows]
    by_date = {}
    for row in rows:
        by_date.setdefault(_row_date(row), row)
    next_id = max(int(r["id"]) for r in rows) + 1
    created_at = format_timestamp(now.astimezone(NEW_YORK).replace(tzinfo=None))
    changed = set()

    # The whole fetched window (anchor included) is compared with stored
    # values to detect upstream revisions; only dates after the anchor are
    # filled or appended.
    for day, values in fetched.iterrows():
        # A trading row needs prices; a sentiment-only day (holiday) needs a score.
        has_prices = not values[PRICE_COLUMNS + ["vix"]].isna().any()
        if day in by_date:
            row = by_date[day]
            for column in VALUE_COLUMNS:
                value = values.get(column)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    continue
                if _is_blank(row.get(column)) and day > anchor:
                    row[column] = _format_value(column, value)
                    report.filled_cells += 1
                    changed.add(day)
                elif not _is_blank(row.get(column)):
                    _record_revision(report, day, column, row[column], value)
            continue
        if day <= anchor:
            continue
        trading = is_trading_day(day)
        if trading and not has_prices:
            continue  # reported by _missing_price_warnings
        if pd.isna(values.get("fear_greed_index")):
            if trading:
                report.warnings.append(f"{day}: no Fear & Greed score yet; row not appended")
            continue
        row = {"id": str(next_id), "date": format_date(day), "created_at": created_at}
        for column in VALUE_COLUMNS:
            if trading or column not in PRICE_COLUMNS + ["vix"]:
                row[column] = _format_value(column, values[column])
            else:
                row[column] = ""  # weekend or holiday: score only, no prices
        rows.append(row)
        by_date[day] = row
        report.appended_dates.append(day)
        changed.add(day)
        next_id += 1

    report.warnings.extend(_missing_price_warnings(fetched, by_date, anchor, settled))
    validate(rows, settled, changed)
    return rows, report


def _missing_price_warnings(fetched, by_date, anchor, settled):
    """Name each settled trading day after the anchor that was not stored for lack of prices.

    Such a day is retried on the next run (the fetch window starts before the
    anchor), so a warning is the only sign that a source is lagging.
    Weekends and market holidays never have prices and are not reported.
    """
    warnings = []
    day = anchor + timedelta(days=1)
    while day <= settled:
        if is_trading_day(day) and day not in by_date:
            columns = PRICE_COLUMNS + ["vix"]
            if day in fetched.index:
                missing = [c for c in columns if pd.isna(fetched.at[day, c])]
            else:
                missing = columns
            if len(missing) == len(columns):
                warnings.append(f"{day}: no prices from Yahoo yet; row not appended, "
                                "retried on the next run")
            elif missing:
                warnings.append(f"{day}: missing {', '.join(missing)} from Yahoo; row not "
                                "appended, retried on the next run")
        day += timedelta(days=1)
    return warnings


def _record_revision(report, day, column, stored, fetched_value):
    if column == "fear_greed_rating":
        return
    stored = float(stored)
    if column == "fear_greed_index":
        revised = abs(stored - fetched_value) > REVISION_TOLERANCE["fear_greed_index"]
    else:
        revised = abs(stored / fetched_value - 1) > REVISION_TOLERANCE["price"]
    if revised:
        report.revisions_ignored.append({
            "date": day.isoformat(), "column": column,
            "stored": stored, "source": round(float(fetched_value), 4),
        })


def validate(rows, settled, changed):
    """Raise DataQualityError if the merged file fails any check.

    File-level invariants (unique dates and ids, nothing after the settled
    session) cover every row; value checks cover only ``changed`` dates, so
    a genuine historical move cannot block future updates.
    """
    dates = [_row_date(r) for r in rows]
    duplicates = sorted(d for d, n in Counter(dates).items() if n > 1)
    if duplicates:
        raise DataQualityError(f"duplicate dates: {duplicates[:5]}")
    if max(dates) > settled:
        raise DataQualityError(f"row dated {max(dates)} is after the last settled session {settled}")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise DataQualityError("duplicate ids")

    full = _numeric(rows)
    changed_index = full.index.isin(list(changed))
    frame = full[changed_index]
    problems = []
    score = frame["fear_greed_index"].dropna()
    if ((score < 0) | (score > 100)).any():
        problems.append("Fear & Greed outside 0-100")
    ratings = {r["fear_greed_rating"] for r in rows
               if _row_date(r) in changed and not _is_blank(r.get("fear_greed_rating"))}
    if ratings - RATINGS:
        problems.append(f"unknown ratings {sorted(ratings - RATINGS)}")
    vix = frame["vix"].dropna()
    if ((vix < 5) | (vix > 150)).any():
        problems.append("VIX outside 5-150")
    # Day-over-day moves are measured against the previous stored value,
    # which may be an older row, so the series comes from the full file.
    for column in PRICE_COLUMNS:
        if (frame[column].dropna() <= 0).any():
            problems.append(f"{column} has non-positive prices")
        moves = full[column].dropna().pct_change().abs()
        jumps = moves[moves.index.isin(list(changed)) & (moves > MAX_EQUITY_MOVE)]
        if not jumps.empty:
            problems.append(f"{column} moved >{MAX_EQUITY_MOVE:.0%} on {jumps.index[0]}")
    full_vix = full["vix"].dropna()
    ratio = (full_vix / full_vix.shift()).dropna()
    ratio = ratio[ratio.index.isin(list(changed))]
    if ((ratio > MAX_VIX_RATIO) | (ratio < 1 / MAX_VIX_RATIO)).any():
        problems.append("VIX changed more than 4x in one day")
    if problems:
        raise DataQualityError("; ".join(problems))
