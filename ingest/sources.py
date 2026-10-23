"""Fetch daily closes from Yahoo Finance and the CNN Fear & Greed history."""
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    # CNN answers 418 ("You're a bot") without a browser-like origin.
    "Referer": "https://edition.cnn.com/",
    "Origin": "https://edition.cnn.com",
}
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"

# CSV column -> (Yahoo symbol, use dividend-adjusted close).
# The historical file was built from yfinance adjusted closes for the ETFs.
YAHOO_COLUMNS = {
    "qqq_close": ("QQQ", True),
    "smh_close": ("SMH", True),
    "spx_close": ("^GSPC", False),
    "dji_close": ("^DJI", False),
    "vix": ("^VIX", False),
}


class SourceError(RuntimeError):
    pass


def _get_json(url, session, **kwargs):
    response = session.get(url, headers=BROWSER_HEADERS, timeout=30, **kwargs)
    if response.status_code != 200:
        raise SourceError(f"{url} returned HTTP {response.status_code}")
    return response.json()


def fetch_yahoo_closes(symbol, start, end, adjusted, session=None):
    """Daily closes for one symbol, indexed by exchange-local trading date."""
    session = session or requests.Session()
    period1 = int(datetime.combine(start, datetime.min.time(), timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), datetime.min.time(),
                                   timezone.utc).timestamp())
    payload = _get_json(
        YAHOO_URL.format(symbol=symbol), session,
        params={"period1": period1, "period2": period2, "interval": "1d"},
    )
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result or not result.get("timestamp"):
        raise SourceError(f"Yahoo returned no rows for {symbol}")
    tz = result["meta"].get("exchangeTimezoneName", "America/New_York")
    dates = (pd.to_datetime(result["timestamp"], unit="s", utc=True)
             .tz_convert(tz).date)
    indicators = result["indicators"]
    if adjusted:
        values = indicators["adjclose"][0]["adjclose"]
    else:
        values = indicators["quote"][0]["close"]
    series = pd.Series(values, index=pd.Index(dates, name="date"), name=symbol, dtype="float64")
    return series[~series.index.duplicated(keep="last")].dropna()


def fetch_yahoo_frame(start, end, session=None):
    session = session or requests.Session()
    columns = {
        column: fetch_yahoo_closes(symbol, start, end, adjusted, session)
        for column, (symbol, adjusted) in YAHOO_COLUMNS.items()
    }
    return pd.DataFrame(columns).sort_index()


def fetch_cnn_fear_greed(start, session=None):
    """Daily Fear & Greed scores from ``start`` onward, indexed by date.

    History points are stamped at 00:00 UTC of their session date; an
    intraday "live" point with a non-midnight timestamp is dropped.
    """
    session = session or requests.Session()
    payload = _get_json(CNN_URL.format(start=start.isoformat()), session)
    points = payload.get("fear_and_greed_historical", {}).get("data") or []
    rows = []
    for point in points:
        stamp = pd.Timestamp(point["x"], unit="ms", tz="UTC")
        if stamp != stamp.normalize():
            continue
        rows.append({"date": stamp.date(), "fear_greed_index": float(point["y"]),
                     "fear_greed_rating": point.get("rating")})
    if not rows:
        raise SourceError("CNN returned no Fear & Greed history")
    frame = pd.DataFrame(rows).drop_duplicates("date", keep="last").set_index("date")
    return apply_live_readings(frame.sort_index(), payload.get("fear_and_greed") or {})


def apply_live_readings(history, live):
    """Use CNN's headline fields for the two most recent sessions.

    CNN keeps updating its newest history points after the close (on
    2026-10-21 the 10-20 point read 28.06 while ``previous_close`` was 27.2),
    so: the session on the live reading's New York date takes ``score``, and
    the session before it takes ``previous_close``.
    """
    if "timestamp" not in live:
        return history
    history = history.copy()
    live_date = pd.Timestamp(live["timestamp"]).tz_convert("America/New_York").date()
    if live.get("score") is not None and live_date in history.index:
        history.loc[live_date, ["fear_greed_index", "fear_greed_rating"]] = [
            float(live["score"]), live.get("rating") or score_to_rating(live["score"])
        ]
    earlier = history.index[history.index < live_date]
    if live.get("previous_close") is not None and len(earlier):
        score = float(live["previous_close"])
        history.loc[earlier[-1], ["fear_greed_index", "fear_greed_rating"]] = [
            score, score_to_rating(score)
        ]
    return history


def score_to_rating(score):
    """CNN's rating bands for a 0-100 Fear & Greed score."""
    if score < 25:
        return "extreme fear"
    if score < 45:
        return "fear"
    if score <= 55:
        return "neutral"
    if score < 75:
        return "greed"
    return "extreme greed"


def fetch_all(start, end, session=None):
    """Prices and sentiment for start..end, outer-joined on date."""
    session = session or requests.Session()
    prices = fetch_yahoo_frame(start, end, session)
    sentiment = fetch_cnn_fear_greed(start, session)
    sentiment = sentiment[sentiment.index <= end]
    return prices.join(sentiment, how="outer")
