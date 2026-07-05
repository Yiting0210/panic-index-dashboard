import numpy as np
import streamlit as st

RISK_FREE_RATE = 0.04 / 252

def max_drawdown(series):
    """Calculate maximum peak-to-trough drawdown."""
    peak = series.cummax()
    return ((series - peak) / peak).min() * 100

def sharpe_ratio(returns):
    """Calculate annualized Sharpe Ratio."""
    excess = returns.fillna(0) - RISK_FREE_RATE
    return excess.mean() / excess.std() * np.sqrt(252) if excess.std() > 0 else 0.0



def render_kpi_row(bt, max_drawdown_fn, sharpe_ratio_fn):
    """Render 6-column KPI metrics."""
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Strategy Return",   f"{bt['cumulative_strategy'].iloc[-1]-100:.1f}%")
    c2.metric("Buy & Hold Return", f"{bt['cumulative_buyhold'].iloc[-1]-100:.1f}%")
    c3.metric("Strategy Max DD",   f"{max_drawdown_fn(bt['cumulative_strategy']):.1f}%")
    c4.metric("B&H Max DD",        f"{max_drawdown_fn(bt['cumulative_buyhold']):.1f}%")
    c5.metric("Strategy Sharpe",   f"{sharpe_ratio_fn(bt['strategy_return']):.3f}")
    c6.metric("B&H Sharpe",        f"{sharpe_ratio_fn(bt['daily_return']):.3f}")



