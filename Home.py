
import streamlit as st
from utils import add_sidebar_nav
from utils import load_data, render_scatter_plot

# ── 缓存计算 ──────────────────────────────────────────────────────────────────
@st.cache_data
def prepare_scatter_data():
    df = load_data()
    indices = ['qqq_close', 'smh_close', 'spx_close', 'dji_close']
    result = {}
    for col in indices:
        dff = df.copy()
        dff['ma50'] = dff[col].rolling(window=50).mean()
        dff['price_dev_pct'] = (dff[col] - dff['ma50']) / dff['ma50'] * 100
        result[col] = dff
    return result

@st.cache_resource
def prepare_all_figures():
    data = prepare_scatter_data()
    figures = {}
    index_list = [
        ('qqq_close', 'Nasdaq 100 (QQQ)'),
        ('smh_close', 'Semiconductors (SMH)'),
        ('spx_close', 'S&P 500 (SPX)'),
        ('dji_close', 'Dow Jones (DJI)'),
    ]
    for col_name, label in index_list:
        fig = render_scatter_plot(data[col_name], col_name, label)
        fig.update_layout(
            height=300,
            title=dict(text=label, font=dict(size=13), x=0.5, xanchor='center'),
            margin=dict(t=40, b=40, l=40, r=40)
        )
        figures[col_name] = fig
    return figures

# ── Page config ─────────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Market Sentiment Dashboard")

# ── Sidebar: Navigator ───────────────────────────────────────────────────────────────────
add_sidebar_nav()

st.title("Market Sentiment & Panic Index Research Dashboard")
st.markdown("""
A market research dashboard for studying sentiment stress, volatility, and
price behavior across major indices and the semiconductor sector.
""")
st.subheader("🔍 What This Dashboard Does")
st.markdown("""
This dashboard turns fragmented sentiment and volatility data into a structured
workflow for studying market stress regimes and subsequent returns.

The **Composite Panic Index** combines VIX and CNN Fear & Greed data to help
users:

- identify panic and greed regimes,
- evaluate forward returns after extreme sentiment periods,
- test allocation rules against Buy & Hold,
- distinguish causal signals from hindsight validation labels.

Before using the strategy pages, the home view checks the core assumption:
**Does market sentiment have a meaningful relationship with price deviation
from trend across different equity markets?**

The scatter plots below compare Fear & Greed Index with price deviation from
MA50 across QQQ, SMH, SPX, and DJI. Color and marker size represent VIX levels.
""")




# ── 4 Charts────────────────────────────────────────────────────────
placeholder = st.empty()
placeholder.info("⏳ Loading charts for the first time, please wait...")

with st.spinner("Loading charts..."):
    all_figures = prepare_all_figures()


col1, col2 = st.columns(2)

indices = [
    ('qqq_close', 'Nasdaq 100 (QQQ)'),
    ('smh_close', 'Semiconductors (SMH)'),
    ('spx_close', 'S&P 500 (SPX)'),
    ('dji_close', 'Dow Jones (DJI)'),
]

col1, col2 = st.columns(2)
for i, (col_name, label) in enumerate(indices):
    if i % 2 == 0:
        with col1:
            st.plotly_chart(all_figures[col_name],
                          width="stretch",
                          key=f"scatter_{col_name}")
    else:
        with col2:
            st.plotly_chart(all_figures[col_name],
                          width="stretch",
                          key=f"scatter_{col_name}")

placeholder.empty()

st.markdown("""
**Observation**: Visual inspection suggests a recurring relationship between
sentiment conditions and price position relative to MA50 across the selected
markets. Extreme fear observations tend to appear more frequently below trend,
while greed observations appear more frequently above trend. This is exploratory
evidence and should be evaluated together with the strategy, forward-return,
and validation analyses.
""")

st.divider()

st.markdown("""
### 💡 How to Use the Product

The app has two complementary research workflows:

| | 💠 Threshold Strategy | ⚡ Peak Detection & Signal Validation |
|---|---|---|
| **Role** | Primary allocation research workflow | Research validation workflow |
| **Purpose** | Regime-based accumulation and risk management | Validate historical peaks and study causal confirmation signals |
| **Signal logic** | Panic Index buy/sell zones | `scipy.find_peaks` labels + `RealTimePeakDetector` signals |
| **Interpretation** | Panic-zone days are exposure days, not single-day forecasts | scipy is hindsight-only; RealTime uses past/current data |
| **Best for** | Portfolio allocation rules and drawdown-aware backtests | Signal QA, research review, and tactical signal exploration |

Use **Threshold Strategy** first when evaluating the primary allocation logic.
Use **Peak Detection & Signal Validation** to understand whether Panic Index
extremes historically aligned with forward returns and whether causal
panic-reversal signals add useful research context.

Interpret results safely: forward returns are historical observations, not
guarantees; scipy labels are not deployable signals; RealTime analysis uses the
underlying index/ETF as a directional proxy rather than a full options-pricing
backtest.

👈 Select a workflow from the sidebar to explore.
""")
