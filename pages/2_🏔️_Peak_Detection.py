import streamlit as st
from plotly.subplots import make_subplots

from utils import load_data, load_filtered_data
from utils import (add_sidebar_nav, render_controls, render_threshold_selector,
                   render_horizon_selector, render_realtime_params,
                   render_scipy_params)
from utils import render_forward_return_histogram, render_forward_return_table
from utils import (prepare_panic_signal, build_strategy_comparison_table,
                   build_signal_lines, build_watch_zone_shapes,
                   add_price_traces, add_panic_signal_trace,
                   add_peak_markers, apply_peak_chart_layout)
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
dff, signal_label, signal_col = prepare_panic_signal(dff, use_zscore)

tabs = st.tabs([
    "Overview",
    "Historical Peaks: scipy.find_peaks",
    "Real-Time Signals: causal detector",
    "Comparison",
])

with tabs[0]:
    st.markdown(
        """
- `scipy.find_peaks` is a hindsight historical validation method.
- `RealTimePeakDetector` is causal and only uses past/current data.
- scipy results should not be interpreted as directly tradable signals.
        """
    )

with tabs[1]:
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
        shapes = build_signal_lines(scipy_peaks['date_dt'], "rgba(147,51,234,0.3)")
        fig1.update_layout(shapes=shapes)

        # Price + MA lines
        add_price_traces(fig1, dff, target_col)

        # Panic signal area
        add_panic_signal_trace(fig1, dff, signal_label)

        # Peak markers on signal chart
        add_peak_markers(fig1, scipy_peaks, "scipy Peak", '#9333EA')

        # Threshold reference line
        fig1.add_hline(y=buy_threshold, line_color="red", line_dash="dash",
                       annotation_text=f"Threshold Strategy Buy Zone ({buy_threshold})",
                       row=2, col=1)

        apply_peak_chart_layout(fig1, signal_label)
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

with tabs[2]:
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
        shapes2 = build_signal_lines(rt_peaks['date_dt'], "rgba(249,115,22,0.3)")

        # Watch zone highlight (entry_threshold and above)
        shapes2 += build_watch_zone_shapes(dff, entry_threshold)

        fig2.update_layout(shapes=shapes2)

        # Price + MA
        add_price_traces(fig2, dff, target_col)

        # Panic signal area
        add_panic_signal_trace(fig2, dff, signal_label)

        # Entry threshold line
        fig2.add_hline(y=entry_threshold, line_color="orange", line_dash="dash",
                       annotation_text=f"Watch Zone ({entry_threshold})", row=2, col=1)

        # RealTime signal markers
        add_peak_markers(fig2, rt_peaks, "RealTime Signal", '#F97316')

        apply_peak_chart_layout(fig2, signal_label)
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

with tabs[3]:
    st.subheader("📊 Strategy Comparison — Threshold vs scipy vs RealTime")

    df_comparison = build_strategy_comparison_table(
        dff, target_col, buy_threshold, scipy_mask, rt_mask
    )

    if not df_comparison.empty:
        st.dataframe(df_comparison, use_container_width=True, hide_index=True)
        st.caption(
            "Direct comparison of signal quality across three detection methods. "
            "Higher Win Rate and Avg Ret indicates stronger signal edge."
        )
