import streamlit as st
import pandas as pd
import os

CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "market_data_clean.csv"
)
PRICE_COLS = ['qqq_close', 'smh_close', 'spx_close', 'dji_close', 'vix']


def _score_to_rating(score):
    if pd.isna(score):
        return None
    if score < 25:
        return "extreme fear"
    elif score < 45:
        return "fear"
    elif score < 55:
        return "neutral"
    elif score < 75:
        return "greed"
    else:
        return "extreme greed"


# ── Data Loading & Processing ─────────────────────────────────────────────────
def read_market_data(csv_path=CSV_PATH, as_of=None, fill_method='linear'):
    """Read and clean the raw market CSV without Streamlit or derived signals.

    ``as_of`` drops rows dated after it *before* gaps are filled, and
    ``fill_method='ffill'`` fills missing prices from earlier rows only.
    The dashboard keeps linear interpolation, which borrows the next
    observed price and is therefore not point-in-time safe.
    """
    df = pd.read_csv(csv_path)

    df['date_dt']   = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df['date_only'] = df['date_dt'].dt.date
    df = df.sort_values('date_dt')
    df = df.drop_duplicates(subset=['date_only'], keep='last')
    df = df.dropna(subset=['date_dt'])
    if as_of is not None:
        df = df[df['date_dt'] <= pd.Timestamp(as_of)].copy()

    if fill_method == 'ffill':
        df[PRICE_COLS] = df[PRICE_COLS].ffill()
    else:
        df[PRICE_COLS] = df[PRICE_COLS].interpolate(method='linear')

    df['fear_greed_rating'] = df.apply(
        lambda row: _score_to_rating(row['fear_greed_index'])
        if pd.isna(row.get('fear_greed_rating')) else row['fear_greed_rating'],
        axis=1
    )

    for col in ['qqq', 'smh', 'spx', 'dji']:
        df[f'{col}_return'] = df[f'{col}_close'].pct_change(fill_method=None) * 100

    return df


def compute_panic_index(df):
    """Add the Composite Panic Index using VIX min/max of the rows passed in.

    The VIX normalization depends on the min and max of the supplied frame,
    so callers that need point-in-time values must truncate the frame to the
    as-of date before calling this function.
    """
    df = df.copy()
    df['vix_norm']    = (df['vix'] - df['vix'].min()) / (df['vix'].max() - df['vix'].min()) * 100
    df['fg_fear']     = 100 - df['fear_greed_index']
    df['panic_index'] = df['vix_norm'] * 0.5 + df['fg_fear'] * 0.5
    return df


def data_version(csv_path=CSV_PATH):
    """Modification time of the CSV, used as a cache key.

    The daily update rewrites the CSV in place; keying caches on its mtime
    makes a running app pick up new rows without a restart.
    """
    return os.stat(csv_path).st_mtime_ns


@st.cache_data(max_entries=2)
def _load_data(version):
    return compute_panic_index(read_market_data())


def load_data():
    return _load_data(data_version())


@st.cache_data(max_entries=64)
def _load_filtered_data(start_date, end_date, target_col, version):
    df = _load_data(version)
    dff = df[
        (df['date_dt'].dt.date >= start_date) &
        (df['date_dt'].dt.date <= end_date)
    ].copy()
    dff['ma50']  = dff[target_col].rolling(window=50).mean()
    dff['ma200'] = dff[target_col].rolling(window=200).mean()
    dff['price_dev_pct'] = (dff[target_col] - dff['ma50']) / dff['ma50'] * 100
    return dff


def load_filtered_data(start_date, end_date, target_col):
    """Load and filter data with MA calculations cached per combination."""
    return _load_filtered_data(start_date, end_date, target_col, data_version())
