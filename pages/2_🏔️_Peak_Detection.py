import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from utils import load_data, load_filtered_data
from utils import (add_sidebar_nav, render_controls, render_threshold_selector,
                   render_horizon_selector, render_realtime_params,
                   render_scipy_params)
from utils import render_forward_return_histogram, render_forward_return_table
from utils.peak_detection import detect_peaks_scipy, detect_peaks_realtime

st.set_page_config(layout="wide", page_title="Peak Detection")

# ── Load data ─────────────────────────────────────────────────────────────────
df = load_data()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("⚡ Peak Detection Strategy")
st.markdown(
    "Two approaches to detect Panic Index peaks without fixed thresholds: "
    "**scipy.find_peaks** (historical) and **RealTimePeakDetector** (causal, no lookahead bias)."
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
add_sidebar_nav()
buy_threshold, sell_threshold = render_threshold_selector()
selected_label, target_col, date_range = render_controls(df)
prominence, distance = render_scipy_params()
entry_threshold, fall_back_pct, use_zscore = render_realtime_params()
horizons = render_horizon_selector()


# ── Filter data ───────────────────────────────────────────────────────────────
if len(date_range) == 2:
    dff = load_filtered_data(date_range[0], date_range[1], target_col)
else:
    dff = load_filtered_data(df['date_dt'].min().date(),
                             df['date_dt'].max().date(), target_col)

# ── Rolling Z-Score (optional) ────────────────────────────────────────────────
if use_zscore:
    roll_mean = dff['panic_index'].rolling(252).mean()
    roll_std  = dff['panic_index'].rolling(252).std()
    dff['panic_signal'] = (dff['panic_index'] - roll_mean) / roll_std.replace(0, np.nan)
    signal_label = "Rolling Z-Score (252-day)"
    signal_col   = "panic_signal"
else:
    dff['panic_signal'] = dff['panic_index']
    signal_label = "Composite Panic Index"
    signal_col   = "panic_signal"

# ════════════════════════════════════════════════════════════════════════════
# PART 1: scipy.find_peaks
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("🔬 Part 1: Historical Peak Detection — scipy.find_peaks")
st.caption(
    "Detects local maxima in the full historical Panic Index. "
    "Not causal — uses full dataset. Best for validating signal quality in backtests."
)

placeholder1 = st.empty()
placeholder1.info("⏳ Detecting peaks...")

with st.spinner("Running scipy peak detection..."):

    scipy_peaks = detect_peaks_scipy(
        dff, prominence=prominence, distance=distance, signal_col=signal_col
    )
    scipy_mask  = dff.index.isin(scipy_peaks.index)

    st.markdown(f"**{len(scipy_peaks)} peaks detected** with prominence={prominence}, distance={distance} days")

    # ── Chart 1A: Price + Peak Signals ───────────────────────────────────────
    fig1 = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            f"Price & MA50/MA200 — {selected_label}  🟣 scipy Peak Signal",
            f"{signal_label}"
        ),
        row_heights=[0.6, 0.4]
    )

    # Peak signal vertical lines on price chart
    shapes = []
    for d in scipy_peaks['date_dt']:
        shapes.append(dict(
            type="line", x0=d, x1=d, y0=0, y1=1,
            xref="x", yref="y domain",
            line=dict(color="rgba(147,51,234,0.3)", width=1.5),
        ))
    fig1.update_layout(shapes=shapes)

    # Price + MA lines
    fig1.add_trace(go.Scatter(x=dff['date_dt'], y=dff[target_col],
                              name="Price", line=dict(color='black', width=2)), row=1, col=1)
    fig1.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma50'],
                              name="MA50", line=dict(color='orange', dash='dot')), row=1, col=1)
    fig1.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma200'],
                              name="MA200", line=dict(color='blue', dash='dot', width=1.5)), row=1, col=1)

    # Panic signal area
    fig1.add_trace(go.Scatter(
        x=dff['date_dt'], y=dff['panic_signal'],
        name=signal_label,
        line=dict(color='#aaaaaa', width=1),
        fill='tozeroy', fillcolor='rgba(170,170,170,0.1)'
    ), row=2, col=1)

    # Peak markers on signal chart
    fig1.add_trace(go.Scatter(
        x=scipy_peaks['date_dt'], y=scipy_peaks['panic_signal'],
        mode='markers', name="scipy Peak",
        marker=dict(color='#9333EA', size=10, symbol='triangle-down')
    ), row=2, col=1)

    # Threshold reference line
    fig1.add_hline(y=buy_threshold, line_color="red", line_dash="dash",
                   annotation_text=f"Threshold Strategy Buy Zone ({buy_threshold})",
                   row=2, col=1)

    fig1.update_layout(
        height=700, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=80, b=40, l=60, r=40)
    )
    fig1.update_xaxes(tickformat="%b %Y", nticks=15, tickangle=45,
                      hoverformat="%b %d, %Y")
    fig1.update_yaxes(title_text="Price", row=1, col=1)
    fig1.update_yaxes(title_text=signal_label, row=2, col=1)
    st.plotly_chart(fig1, use_container_width=True, key="scipy_chart")

    st.divider()

    # ── Chart 1B: Forward Return Analysis ────────────────────────────────────
    st.markdown("#### 📊 Forward Return Analysis — scipy Peaks")
    col_chart, col_table = st.columns([2, 1])
    with col_chart:
        st.markdown("**Return Distribution After scipy Peak Signals**")
        fig_h1 = render_forward_return_histogram(dff, scipy_mask, target_col, horizons)
        st.plotly_chart(fig_h1, use_container_width=True, key="hist_scipy")
    with col_table:
        st.markdown("**Statistical Edge Summary**")
        st.markdown("🟣 scipy Peak Signals")
        render_forward_return_table(dff, scipy_mask, target_col, horizons)

placeholder1.empty()

# ════════════════════════════════════════════════════════════════════════════
# PART 2: RealTimePeakDetector
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("⚡ Part 2: Real-Time Peak Detection — Causal Algorithm")
st.caption(
    "Simulates real-time signal detection using trailing max + drawdown trigger. "
    "No lookahead bias — each signal is generated using only past data."
)

placeholder2 = st.empty()
placeholder2.info("⏳ Running real-time simulation...")

with st.spinner("Running real-time peak detection..."):

    rt_peaks  = detect_peaks_realtime(
        dff,
        entry_threshold=entry_threshold,
        fall_back_pct=fall_back_pct,
        signal_col=signal_col,
    )
    rt_mask   = dff.index.isin(rt_peaks.index)

    st.markdown(
        f"**{len(rt_peaks)} signals detected** with "
        f"entry_threshold={entry_threshold}, fallback={fall_back_pct*100:.0f}%"
    )

    # ── Chart 2A: Price + RealTime Signals ───────────────────────────────────
    fig2 = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            f"Price & MA50/MA200 — {selected_label}  🟠 RealTime Peak Signal",
            f"{signal_label} — Watch Zone & Signals"
        ),
        row_heights=[0.6, 0.4]
    )

    # RealTime signal vertical lines
    shapes2 = []
    for d in rt_peaks['date_dt']:
        shapes2.append(dict(
            type="line", x0=d, x1=d, y0=0, y1=1,
            xref="x", yref="y domain",
            line=dict(color="rgba(249,115,22,0.3)", width=1.5),
        ))

    # Watch zone highlight (entry_threshold and above)
    watch_zone_dates = dff[dff['panic_signal'] >= entry_threshold]['date_dt']
    for d in watch_zone_dates:
        shapes2.append(dict(
            type="rect",
            x0=d, x1=d,
            y0=entry_threshold, y1=dff['panic_signal'].max(),
            xref="x2", yref="y2",
            fillcolor="rgba(249,115,22,0.05)",
            line_width=0,
        ))

    fig2.update_layout(shapes=shapes2)

    # Price + MA
    fig2.add_trace(go.Scatter(x=dff['date_dt'], y=dff[target_col],
                              name="Price", line=dict(color='black', width=2)), row=1, col=1)
    fig2.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma50'],
                              name="MA50", line=dict(color='orange', dash='dot')), row=1, col=1)
    fig2.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma200'],
                              name="MA200", line=dict(color='blue', dash='dot', width=1.5)), row=1, col=1)

    # Panic signal area
    fig2.add_trace(go.Scatter(
        x=dff['date_dt'], y=dff['panic_signal'],
        name=signal_label,
        line=dict(color='#aaaaaa', width=1),
        fill='tozeroy', fillcolor='rgba(170,170,170,0.1)'
    ), row=2, col=1)

    # Entry threshold line
    fig2.add_hline(y=entry_threshold, line_color="orange", line_dash="dash",
                   annotation_text=f"Watch Zone ({entry_threshold})", row=2, col=1)

    # RealTime signal markers
    fig2.add_trace(go.Scatter(
        x=rt_peaks['date_dt'], y=rt_peaks['panic_signal'],
        mode='markers', name="RealTime Signal",
        marker=dict(color='#F97316', size=10, symbol='triangle-down')
    ), row=2, col=1)

    fig2.update_layout(
        height=700, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=80, b=40, l=60, r=40)
    )
    fig2.update_xaxes(tickformat="%b %Y", nticks=15, tickangle=45,
                      hoverformat="%b %d, %Y")
    fig2.update_yaxes(title_text="Price", row=1, col=1)
    fig2.update_yaxes(title_text=signal_label, row=2, col=1)
    st.plotly_chart(fig2, use_container_width=True, key="realtime_chart")

    st.divider()

    # ── Chart 2B: Forward Return Analysis ────────────────────────────────────
    st.markdown("#### 📊 Forward Return Analysis — RealTime Peaks")
    col_chart2, col_table2 = st.columns([2, 1])
    with col_chart2:
        st.markdown("**Return Distribution After RealTime Peak Signals**")
        fig_h2 = render_forward_return_histogram(dff, rt_mask, target_col, horizons)
        st.plotly_chart(fig_h2, use_container_width=True, key="hist_rt")
    with col_table2:
        st.markdown("**Statistical Edge Summary**")
        st.markdown("🟠 RealTime Peak Signals")
        render_forward_return_table(dff, rt_mask, target_col, horizons)

placeholder2.empty()

# ════════════════════════════════════════════════════════════════════════════
# PART 3: Strategy Comparison
# ════════════════════════════════════════════════════════════════════════════
st.divider()
st.subheader("📊 Strategy Comparison — Threshold vs scipy vs RealTime")

threshold_mask = dff['panic_index'] > buy_threshold

comparison_data = []
for mask, label, horizons_list in [
    (threshold_mask, f"Threshold (>{buy_threshold})",  [1, 5, 21, 63]),
    (scipy_mask,     "scipy Peaks",      [1, 5, 21, 63]),
    (rt_mask,        "RealTime Peaks",   [1, 5, 21, 63]),
]:
    for h in horizons_list:
        fwd = (dff[target_col].shift(-h) / dff[target_col] - 1) * 100
        returns = fwd[mask].dropna()
        if not returns.empty:
            horizon_label = {1: '1 Day', 5: '1 Week', 21: '1 Month', 63: '3 Months'}[h]
            comparison_data.append({
                "Strategy":  label,
                "Horizon":   horizon_label,
                "Signals":   len(returns),
                "Win Rate":  f"{(returns > 0).mean():.1%}",
                "Avg Ret":   f"{returns.mean():.2f}%",
                "Max":       f"{returns.max():.2f}%",
                "Min":       f"{returns.min():.2f}%",
            })

if comparison_data:
    df_comparison = pd.DataFrame(comparison_data)
    st.dataframe(df_comparison, use_container_width=True, hide_index=True)
    st.caption(
        "Direct comparison of signal quality across three detection methods. "
        "Higher Win Rate and Avg Ret indicates stronger signal edge."
    )
