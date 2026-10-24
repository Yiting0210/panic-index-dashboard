"""Append-only market data ingestion rules, tested on synthetic data."""
import os
import sys
from datetime import date, datetime

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ingest import DataQualityError, last_settled_session, merge, read_raw, write_raw
from ingest.market_update import NEW_YORK, format_date

FIELDS = ["id", "date", "fear_greed_index", "fear_greed_rating", "vix", "created_at",
          "qqq_close", "smh_close", "spx_close", "dji_close"]
AFTER_CLOSE = datetime(2026, 10, 9, 18, 30, tzinfo=NEW_YORK)  # Friday evening


def stored_row(id_, day, score=40.0, vix=20.0, qqq=100.0, smh=200.0, spx=5000.0, dji=40000.0):
    def cell(v):
        return "" if v is None else f"{v:g}"
    return {
        "id": str(id_), "date": format_date(day), "fear_greed_index": cell(score),
        "fear_greed_rating": "fear" if score is not None else "", "vix": cell(vix),
        "created_at": "1/10/2026 20:00:00", "qqq_close": cell(qqq), "smh_close": cell(smh),
        "spx_close": cell(spx), "dji_close": cell(dji),
    }


def fetched_frame(rows):
    """rows: {date: (score, vix, qqq, smh, spx, dji)}"""
    records = []
    for day, (score, vix, qqq, smh, spx, dji) in rows.items():
        records.append({"date": day, "fear_greed_index": score,
                        "fear_greed_rating": None if score is None else "fear",
                        "vix": vix, "qqq_close": qqq, "smh_close": smh,
                        "spx_close": spx, "dji_close": dji})
    return pd.DataFrame(records).set_index("date")


def base_rows():
    return [
        stored_row(1, date(2026, 10, 1)),
        stored_row(2, date(2026, 10, 2), qqq=101.0),
        stored_row(3, date(2026, 10, 5), score=None, vix=None, qqq=None, smh=None,
                   spx=None, dji=None),  # placeholder row with nothing filled yet
    ]


def test_appends_new_sessions_and_fills_blank_cells_only():
    rows = base_rows()
    fetched = fetched_frame({
        date(2026, 10, 2): (55.0, 21.0, 102.0, 202.0, 5010, 40100),   # anchor; differs
        date(2026, 10, 5): (41.0, 19.0, 103.0, 203.0, 5020, 40200),
        date(2026, 10, 6): (42.0, 18.5, 104.0, 204.0, 5030, 40300),
    })
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)

    assert report.anchor_date == date(2026, 10, 2)
    assert new_rows[:2] == rows[:2]                       # stored rows untouched
    assert report.appended_dates == [date(2026, 10, 6)]
    assert report.filled_cells == 7                       # every blank on 10-05
    appended = new_rows[-1]
    assert appended["id"] == "4"
    assert appended["date"] == "6/10/2026 00:00:00"
    assert appended["created_at"] == "9/10/2026 18:30:00"


def test_adjusted_prices_are_chain_linked_to_the_stored_anchor():
    rows = base_rows()[:2]
    # Source re-based adjusted closes by 2% (a dividend); index levels unchanged.
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 99.0, 196.0, 5000, 40000),
        date(2026, 10, 5): (41.0, 20.0, 99.99, 197.96, 5050, 40400),
    })
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert report.link_factors["qqq_close"] == pytest.approx(101.0 / 99.0)
    appended = new_rows[-1]
    # 1% fetched return on top of the stored 101.0 anchor.
    assert float(appended["qqq_close"]) == pytest.approx(102.01, abs=0.01)
    assert float(appended["smh_close"]) == pytest.approx(202.0, abs=0.01)
    assert float(appended["spx_close"]) == 5050


def test_conflicting_source_values_are_logged_not_applied():
    rows = base_rows()[:2]
    fetched = fetched_frame({date(2026, 10, 2): (12.0, 20.0, 101.0, 200.0, 5000, 40000)})
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert new_rows == rows
    assert not report.changed


def test_revisions_after_the_anchor_are_reported():
    rows = base_rows()[:2] + [stored_row(3, date(2026, 10, 5), score=30.0)]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (20.0, 20.0, 100.0, 200.0, 5000, 40000),
    })
    rows[2]["qqq_close"] = ""  # leave a gap so 10-05 is not the anchor
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert new_rows[2]["fear_greed_index"] == "30"
    assert report.revisions_ignored == [
        {"date": "2026-10-05", "column": "fear_greed_index", "stored": 30.0, "source": 20.0}
    ]


def test_unsettled_session_is_not_written():
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 9): (41.0, 20.0, 102.0, 201.0, 5010, 40100),
    })
    during_session = datetime(2026, 10, 9, 11, 0, tzinfo=NEW_YORK)
    _, report = merge(rows, fetched, now=during_session)
    assert report.appended_dates == []
    _, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert report.appended_dates == [date(2026, 10, 9)]


def test_weekend_snapshot_is_not_used_as_anchor():
    rows = base_rows()[:2] + [stored_row(3, date(2026, 10, 4), qqq=101.0)]  # Sunday copy
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (41.0, 20.0, 102.0, 201.0, 5010, 40100),
    })
    _, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert report.anchor_date == date(2026, 10, 2)


def test_session_without_fear_greed_waits():
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (None, 20.0, 102.0, 201.0, 5010, 40100),
    })
    _, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert report.appended_dates == []
    assert "no Fear & Greed score" in report.warnings[0]


RUN_AT_1AM = datetime(2026, 10, 6, 1, 0, tzinfo=NEW_YORK)  # Tuesday; settles Monday 10-05


def test_partially_published_session_waits_with_a_warning():
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (41.0, None, 102.0, 201.0, 5010, 40100),  # VIX not published
    })
    _, report = merge(rows, fetched, now=RUN_AT_1AM)
    assert report.appended_dates == []
    assert report.warnings == [
        "2026-10-05: missing vix from Yahoo; row not appended, retried on the next run"
    ]


def test_trading_day_without_any_prices_warns():
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (41.0, None, None, None, None, None),  # score only, Yahoo lagging
    })
    _, report = merge(rows, fetched, now=RUN_AT_1AM)
    assert report.appended_dates == []
    assert report.warnings == [
        "2026-10-05: no prices from Yahoo yet; row not appended, retried on the next run"
    ]


def test_weekends_and_holidays_store_the_score_only_without_warnings():
    rows = [stored_row(1, date(2026, 11, 24)), stored_row(2, date(2026, 11, 25), qqq=101.0)]
    fetched = fetched_frame({
        date(2026, 11, 25): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 11, 26): (41.5, None, None, None, None, None),     # Thanksgiving
        date(2026, 11, 27): (42.0, 19.0, 102.0, 201.0, 5010, 40100),  # Friday session
        date(2026, 11, 28): (42.5, None, None, None, None, None),     # Saturday
    })
    sunday_night = datetime(2026, 11, 30, 1, 0, tzinfo=NEW_YORK)    # settles Sunday 11-29
    new_rows, report = merge(rows, fetched, now=sunday_night)
    assert report.appended_dates == [date(2026, 11, 26), date(2026, 11, 27), date(2026, 11, 28)]
    assert report.warnings == []
    thanksgiving, friday, saturday = new_rows[2:]
    assert thanksgiving["fear_greed_index"] == "41.5"
    assert saturday["fear_greed_rating"] == "fear"
    for row in (thanksgiving, saturday):
        assert all(row[c] == "" for c in ["vix", "qqq_close", "smh_close", "spx_close", "dji_close"])
    assert friday["qqq_close"] == "102"


def test_trading_calendar_matches_nyse_holidays():
    from ingest.trading_calendar import is_trading_day

    closed = [date(2025, 1, 9), date(2025, 4, 18), date(2026, 6, 19), date(2026, 7, 3),
              date(2026, 11, 26), date(2027, 12, 24)]
    open_ = [date(2021, 12, 31), date(2026, 10, 12), date(2026, 11, 11), date(2026, 11, 27)]
    assert not any(is_trading_day(d) for d in closed)
    assert all(is_trading_day(d) for d in open_)
    assert not is_trading_day(date(2026, 10, 24))  # Saturday


def test_complete_session_has_no_warnings():
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (41.0, 20.0, 102.0, 201.0, 5010, 40100),
    })
    _, report = merge(rows, fetched, now=RUN_AT_1AM)
    assert report.appended_dates == [date(2026, 10, 5)]
    assert report.warnings == []


@pytest.mark.parametrize("bad_values, message", [
    ((140.0, 20.0, 102.0, 201.0, 5010, 40100), "Fear & Greed outside"),
    ((41.0, 20.0, 150.0, 201.0, 5010, 40100), "qqq_close moved"),
    ((41.0, 95.0, 102.0, 201.0, 5010, 40100), "VIX changed more than 4x"),
    ((41.0, 20.0, 102.0, 201.0, -5, 40100), "non-positive"),
])
def test_quality_failures_abort_the_batch(bad_values, message):
    rows = base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): bad_values,
    })
    with pytest.raises(DataQualityError, match=message):
        merge(rows, fetched, now=AFTER_CLOSE)


def test_suspicious_chain_link_factor_aborts():
    rows = base_rows()[:2]
    fetched = fetched_frame({date(2026, 10, 2): (40.0, 20.0, 50.5, 200.0, 5000, 40000)})
    with pytest.raises(DataQualityError, match="chain-link factor"):
        merge(rows, fetched, now=AFTER_CLOSE)


def test_update_is_idempotent_and_round_trips_untouched_bytes(tmp_path):
    path = tmp_path / "market.csv"
    write_raw(path, FIELDS, base_rows()[:2])
    original = path.read_bytes()
    fields, rows = read_raw(path)
    write_raw(path, fields, rows)
    assert path.read_bytes() == original

    fetched = fetched_frame({
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
        date(2026, 10, 5): (41.0, 19.0, 102.0, 201.0, 5010, 40100),
    })
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)
    write_raw(path, fields, new_rows)
    assert path.read_bytes().startswith(original)
    _, second = merge(read_raw(path)[1], fetched, now=AFTER_CLOSE)
    assert not second.changed


def test_last_settled_session_cutoff():
    assert last_settled_session(datetime(2026, 10, 9, 16, 29, tzinfo=NEW_YORK)) == date(2026, 10, 8)
    assert last_settled_session(datetime(2026, 10, 9, 16, 30, tzinfo=NEW_YORK)) == date(2026, 10, 9)


def test_cnn_live_fields_override_drifting_history_points():
    from ingest.sources import apply_live_readings

    history = pd.DataFrame(
        {"fear_greed_index": [22.07, 20.80, 28.06, 28.06],
         "fear_greed_rating": ["extreme fear", "extreme fear", "fear", "fear"]},
        index=[date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21)],
    )
    live = {"score": 28.06, "rating": "fear", "previous_close": 27.2,
            "timestamp": "2026-10-21T07:29:01+00:00"}  # 03:29 New York time
    fixed = apply_live_readings(history, live)
    assert fixed.at[date(2026, 10, 20), "fear_greed_index"] == 27.2
    assert fixed.at[date(2026, 10, 21), "fear_greed_index"] == 28.06
    assert fixed.at[date(2026, 10, 19), "fear_greed_index"] == 20.80


def test_cnn_live_reading_on_a_holiday_becomes_that_days_score():
    from ingest.sources import apply_live_readings

    history = pd.DataFrame(
        {"fear_greed_index": [30.0, 31.0], "fear_greed_rating": ["fear", "fear"]},
        index=[date(2026, 11, 24), date(2026, 11, 25)],
    )
    live = {"score": 33.0, "rating": "fear", "previous_close": 31.5,
            "timestamp": "2026-11-26T20:00:00+00:00"}  # Thanksgiving, 15:00 New York
    fixed = apply_live_readings(history, live)
    assert fixed.at[date(2026, 11, 26), "fear_greed_index"] == 33.0
    assert fixed.at[date(2026, 11, 25), "fear_greed_index"] == 31.5
    assert list(fixed.index) == sorted(fixed.index)


def test_cnn_live_reading_on_a_missing_trading_day_is_not_added():
    from ingest.sources import apply_live_readings

    history = pd.DataFrame({"fear_greed_index": [30.0], "fear_greed_rating": ["fear"]},
                           index=[date(2026, 10, 22)])
    live = {"score": 33.0, "rating": "fear", "previous_close": 31.5,
            "timestamp": "2026-10-23T18:00:00+00:00"}  # trading day, still moving
    assert date(2026, 10, 23) not in apply_live_readings(history, live).index


def test_blank_cells_are_missing_not_forward_filled():
    from ingest.market_update import last_complete_date

    rows = base_rows()  # 10-05 is a stored row with every value blank
    assert last_complete_date(rows) == date(2026, 10, 2)


def test_revisions_inside_the_lookback_window_are_reported():
    rows = [stored_row(0, date(2026, 9, 30), score=30.0)] + base_rows()[:2]
    fetched = fetched_frame({
        date(2026, 9, 30): (21.0, 20.0, 100.0, 200.0, 5000, 40000),  # before the anchor
        date(2026, 10, 2): (40.0, 20.0, 101.0, 200.0, 5000, 40000),
    })
    new_rows, report = merge(rows, fetched, now=AFTER_CLOSE)
    assert new_rows == rows
    assert report.revisions_ignored == [
        {"date": "2026-09-30", "column": "fear_greed_index", "stored": 30.0, "source": 21.0}
    ]
