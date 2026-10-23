"""Point-in-time access to the market data used by the research tools."""
import logging
from datetime import date
from functools import lru_cache

import pandas as pd
from mcp.server.mcpserver.exceptions import ToolError

# utils imports Streamlit; keep its runtime warnings out of MCP stdio output.
logging.getLogger("streamlit").setLevel(logging.ERROR)

from utils.data_loader import (CSV_PATH, compute_panic_index,  # noqa: E402
                               data_version, read_market_data)


class ToolInputError(ToolError, ValueError):
    """Bad tool input; MCP forwards the message to the client so it can retry."""


MARKETS = {
    "QQQ": ("qqq_close", "Nasdaq 100 (QQQ)"),
    "SMH": ("smh_close", "Semiconductors (SMH)"),
    "SPX": ("spx_close", "S&P 500 (SPX)"),
    "DJI": ("dji_close", "Dow Jones (DJI)"),
}


class MarketStore:
    """Serves market frames truncated to an as-of date.

    Truncation happens on raw rows before gap filling and before the Panic
    Index normalization, so no value in a returned frame depends on rows
    dated after ``as_of``.
    """

    def __init__(self, csv_path=CSV_PATH):
        self.csv_path = csv_path
        raw_dates = pd.to_datetime(
            pd.read_csv(csv_path, usecols=['date'])['date'],
            dayfirst=True, errors='coerce',
        ).dropna()
        self.first_date = raw_dates.min().date()
        self.last_date = raw_dates.max().date()
        self._frame_as_of = lru_cache(maxsize=64)(self._build_frame)

    def _build_frame(self, as_of):
        df = read_market_data(self.csv_path, as_of=as_of, fill_method='ffill')
        return compute_panic_index(df).reset_index(drop=True)

    def frame_as_of(self, as_of, start_date=None):
        if as_of < self.first_date:
            raise ToolInputError(
                f"as_of {as_of} is before the first available observation "
                f"({self.first_date})."
            )
        df = self._frame_as_of(as_of)
        if start_date is not None:
            df = df[df['date_dt'] >= pd.Timestamp(start_date)]
        return df.reset_index(drop=True).copy()


_store = None
_store_version = None


def get_store():
    """Shared store, rebuilt when the daily update rewrites the CSV."""
    global _store, _store_version
    if _store is None or (_store_version is not None and _store_version != data_version()):
        _store = MarketStore()
        _store_version = data_version()
    return _store


def set_store(store):
    """Inject a store (tests); it is kept until set_store is called again.

    Passing None restores the default store that follows the CSV.
    """
    global _store, _store_version
    _store = store
    _store_version = None


def to_date(value):
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
