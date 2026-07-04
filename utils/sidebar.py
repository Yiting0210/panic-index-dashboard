# utils/sidebar.py
import streamlit as st

def render_sidebar_header(title_text, icon):
    """Standardize the sidebar headings with an icon."""
    st.sidebar.markdown(f"**{icon} {title_text.upper()}**")



def add_sidebar_nav():
    # 统一用粗体 Markdown 替代 Subheader

    render_sidebar_header("NAVIGATION", "🧭")
    st.sidebar.page_link("Home.py", label="Home", icon="🏠")
    st.sidebar.page_link("pages/1_💠_Threshold_Strategy.py", label="Threshold Strategy", icon="💠")
    st.sidebar.page_link("pages/2_🏔️_Peak_Detection.py", label="Peak Detection", icon="🏔️")


def render_controls(df):
    st.sidebar.markdown("---")
    """Render sidebar controls, return selected params."""

    render_sidebar_header("Controls", "🎛️")

    target_map = {
        "Nasdaq 100 (QQQ)":     "qqq_close",
        "Semiconductors (SMH)": "smh_close",
        "S&P 500 (SPX)":        "spx_close",
        "Dow Jones (DJI)":      "dji_close",
    }
    selected_label = st.sidebar.selectbox("Select Index", list(target_map.keys()))
    target_col = target_map[selected_label]

    min_date = df['date_dt'].min().date()
    max_date = df['date_dt'].max().date()
    date_range = st.sidebar.date_input(
        "Date Range", value=(min_date, max_date),
        min_value=min_date, max_value=max_date
    )
    return selected_label, target_col, date_range

def render_threshold_selector():
    """Render buy/sell threshold sliders."""
    st.sidebar.markdown("---")
    render_sidebar_header("Signal Thresholds", "📶")
    buy_threshold = st.sidebar.slider(
        "Buy Zone threshold (Panic Index)",
        min_value=50, max_value=90, value=64, step=1,
        help="Default: 64 (95th percentile of 5-year history)"
    )
    sell_threshold = st.sidebar.slider(
        "Sell Zone threshold (Panic Index)",
        min_value=5, max_value=30, value=15, step=1,
        help="Default: 15 (5th percentile of 5-year history)"
    )
    return buy_threshold, sell_threshold

def render_position_sizing():
    """Render position sizing sliders, return params."""
    st.sidebar.markdown("---")
    render_sidebar_header("Position Sizing", "⚙️")
    pos_initial   = st.sidebar.slider("Initial position (%)", 0, 100, 50, 10)
    add_amount    = st.sidebar.slider("Add on buy signal (%)", 0, 50, 20, 5)
    reduce_amount = st.sidebar.slider("Reduce on sell signal (%)", 0, 50, 10, 5)
    pos_max       = st.sidebar.slider("Max position (%)", 50, 100, 100, 10)
    pos_min       = st.sidebar.slider("Min position (%)", 0, 50, 0, 10)
    return pos_initial, add_amount, reduce_amount, pos_max, pos_min



def render_horizon_selector():
    """Render horizon multi-select for forward return analysis."""
    st.sidebar.markdown("---")
    render_sidebar_header("Analysis Horizons", "⏱️")
    selected_horizons = st.sidebar.multiselect(
        "Select horizons",
        options=[1, 5, 21, 63],
        default=[1, 5, 21, 63],
        format_func=lambda x: {1: '1 Day', 5: '1 Week', 21: '1 Month', 63: '3 Months'}[x]
    )
    return selected_horizons if selected_horizons else [1, 5, 21, 63]

def render_scipy_params():
    """Render scipy.find_peaks parameter sliders."""
    st.sidebar.markdown("---")
    render_sidebar_header("Part 1: scipy Parameters", "🔬")
    prominence = st.sidebar.slider(
        "Prominence", 5, 30, 10, 1,
        help="Minimum height difference between peak and surrounding valleys"
    )
    distance = st.sidebar.slider(
        "Min Distance (days)", 5, 30, 10, 1,
        help="Minimum number of days between two peaks"
    )
    return prominence, distance


def render_realtime_params():
    """Render RealTimePeakDetector parameter sliders."""
    st.sidebar.markdown("---")
    render_sidebar_header("Part 2: RealTime Parameters", "⚡")
    use_zscore = st.sidebar.checkbox(
        "Use Rolling Z-Score (252-day)", value=False,
        help="Normalize Panic Index using rolling Z-Score before detection"
    )
    if use_zscore:
        entry_threshold = st.sidebar.slider(
            "Entry Threshold (Z-Score)", 1.0, 4.0, 2.0, 0.1,
            help="Z-Score level that activates the watch zone"
        )
    else:
        entry_threshold = st.sidebar.slider(
            "Entry Threshold", 60, 95, 80, 1,
            help="Panic Index level that activates the watch zone"
        )
    fall_back_pct = st.sidebar.slider(
        "Fallback % to trigger", 1, 20, 5, 1,
        help="% drop from local max that confirms the peak"
    ) / 100
    return entry_threshold, fall_back_pct, use_zscore


