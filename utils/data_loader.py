import streamlit as st
import pandas as pd
import os

# ── Data Loading & Processing ─────────────────────────────────────────────────
@st.cache_data
def load_data():
    base_path = os.path.dirname(os.path.dirname(__file__))
    csv_path  = os.path.join(base_path, "data", "market_data_clean.csv")
    df = pd.read_csv(csv_path)

    df['date_dt']   = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')
    df['date_only'] = df['date_dt'].dt.date
    df = df.sort_values('date_dt')
    df = df.drop_duplicates(subset=['date_only'], keep='last')
    df = df.dropna(subset=['date_dt'])

    price_cols = ['qqq_close', 'smh_close', 'spx_close', 'dji_close', 'vix']
    df[price_cols] = df[price_cols].interpolate(method='linear')

    def score_to_rating(score):
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

    df['fear_greed_rating'] = df.apply(
        lambda row: score_to_rating(row['fear_greed_index'])
        if pd.isna(row.get('fear_greed_rating')) else row['fear_greed_rating'],
        axis=1
    )

    for col in ['qqq', 'smh', 'spx', 'dji']:
        df[f'{col}_return'] = df[f'{col}_close'].pct_change(fill_method=None) * 100


    df['vix_norm']    = (df['vix'] - df['vix'].min()) / (df['vix'].max() - df['vix'].min()) * 100
    df['fg_fear']     = 100 - df['fear_greed_index']
    df['panic_index'] = df['vix_norm'] * 0.5 + df['fg_fear'] * 0.5

    return df

@st.cache_data
def load_filtered_data(start_date, end_date, target_col):
    """Load and filter data with MA calculations cached per combination."""
    df = load_data()
    dff = df[
        (df['date_dt'].dt.date >= start_date) &
        (df['date_dt'].dt.date <= end_date)
    ].copy()
    dff['ma50']  = dff[target_col].rolling(window=50).mean()
    dff['ma200'] = dff[target_col].rolling(window=200).mean()
    dff['price_dev_pct'] = (dff[target_col] - dff['ma50']) / dff['ma50'] * 100
    return dff
