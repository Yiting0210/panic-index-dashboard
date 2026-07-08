import numpy as np
import pandas as pd
import streamlit as st

DEFAULT_ANNUAL_RISK_FREE_RATE = 0.0
RISK_FREE_RATE = DEFAULT_ANNUAL_RISK_FREE_RATE

def max_drawdown(series):
    """Calculate maximum peak-to-trough drawdown."""
    peak = series.cummax()
    return ((series - peak) / peak).min() * 100

def infer_periods_per_year(returns, dates):
    """Infer observed periods per year from dated return observations."""
    clean = pd.DataFrame({'return': returns, 'date_dt': dates}).dropna()
    if len(clean) < 2:
        return float('nan')

    elapsed_days = (clean['date_dt'].iloc[-1] - clean['date_dt'].iloc[0]).days
    if elapsed_days <= 0:
        return float('nan')

    return len(clean) / (elapsed_days / 365.25)


def sharpe_ratio(
    returns,
    dates=None,
    annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
    periods_per_year=None,
):
    """Calculate annualized Sharpe ratio from arithmetic period returns.

    Missing returns are excluded, including the first pct_change NaN. When
    dates are supplied, annualization is inferred from the observed backtest
    rows over elapsed calendar time so interpolated dashboard rows are handled
    consistently. If dates are omitted, the legacy 252-period convention is
    used.
    """
    clean_returns = returns.dropna()
    if len(clean_returns) < 2:
        return float('nan')

    if periods_per_year is None:
        if dates is not None:
            periods_per_year = infer_periods_per_year(returns, dates)
        else:
            periods_per_year = 252
    if pd.isna(periods_per_year) or periods_per_year <= 0:
        return float('nan')

    period_risk_free_rate = annual_risk_free_rate / periods_per_year
    excess = clean_returns - period_risk_free_rate
    volatility = excess.std()
    if volatility == 0 or pd.isna(volatility):
        return float('nan')

    return excess.mean() / volatility * np.sqrt(periods_per_year)


def annualized_return_from_equity(equity, dates):
    """Annualize cumulative equity return over elapsed calendar dates."""
    clean = pd.DataFrame({'equity': equity, 'date_dt': dates}).dropna()
    if len(clean) < 2:
        return float('nan')

    start_equity = clean['equity'].iloc[0]
    end_equity = clean['equity'].iloc[-1]
    if start_equity <= 0 or end_equity <= 0:
        return float('nan')

    elapsed_days = (clean['date_dt'].iloc[-1] - clean['date_dt'].iloc[0]).days
    if elapsed_days <= 0:
        return float('nan')

    years = elapsed_days / 365.25
    return (end_equity / start_equity) ** (1 / years) - 1


def calmar_ratio(equity, dates):
    """Calculate Calmar ratio from equity curve and dated observations."""
    annualized_return = annualized_return_from_equity(equity, dates)
    drawdown = max_drawdown(equity) / 100
    if pd.isna(annualized_return) or pd.isna(drawdown) or drawdown == 0:
        return float('nan')
    return annualized_return / abs(drawdown)


def build_performance_metrics(
    bt,
    annual_risk_free_rate=DEFAULT_ANNUAL_RISK_FREE_RATE,
):
    """Build canonical backtest performance metrics for display."""
    periods_per_year = infer_periods_per_year(bt['daily_return'], bt['date_dt'])
    return {
        "strategy_return": bt['cumulative_strategy'].iloc[-1] - 100,
        "buyhold_return": bt['cumulative_buyhold'].iloc[-1] - 100,
        "strategy_max_drawdown": max_drawdown(bt['cumulative_strategy']),
        "buyhold_max_drawdown": max_drawdown(bt['cumulative_buyhold']),
        "strategy_sharpe": sharpe_ratio(
            bt['strategy_return'],
            bt['date_dt'],
            annual_risk_free_rate,
            periods_per_year,
        ),
        "buyhold_sharpe": sharpe_ratio(
            bt['daily_return'],
            bt['date_dt'],
            annual_risk_free_rate,
            periods_per_year,
        ),
        "strategy_calmar": calmar_ratio(
            bt['cumulative_strategy'],
            bt['date_dt'],
        ),
        "buyhold_calmar": calmar_ratio(
            bt['cumulative_buyhold'],
            bt['date_dt'],
        ),
        "periods_per_year": periods_per_year,
        "annual_risk_free_rate": annual_risk_free_rate,
    }



def _format_ratio(value):
    return "N/A" if pd.isna(value) else f"{value:.3f}"


def render_kpi_row(bt, performance_metrics=None):
    """Render canonical backtest performance KPI metrics."""
    metrics = performance_metrics or build_performance_metrics(bt)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Strategy Return", f"{metrics['strategy_return']:.1f}%")
    c2.metric("Buy & Hold Return", f"{metrics['buyhold_return']:.1f}%")
    c3.metric("Strategy Max DD", f"{metrics['strategy_max_drawdown']:.1f}%")
    c4.metric("B&H Max DD", f"{metrics['buyhold_max_drawdown']:.1f}%")

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Strategy Sharpe", _format_ratio(metrics['strategy_sharpe']))
    c6.metric("B&H Sharpe", _format_ratio(metrics['buyhold_sharpe']))
    c7.metric("Strategy Calmar", _format_ratio(metrics['strategy_calmar']))
    c8.metric("B&H Calmar", _format_ratio(metrics['buyhold_calmar']))



