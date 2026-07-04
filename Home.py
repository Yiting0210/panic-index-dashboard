
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

st.title("Market Sentiment & Panic Index Dashboard")
st.markdown("""
Exploring Market Asymmetry: Can a Composite Panic Index (VIX + CNN Fear & Greed)
Identify Tactical Entry and Exit Opportunities Across Major Indices and the
Semiconductor Sector (2021–2026)?
""")
st.subheader("🔍 Research Motivation")
st.markdown("""
Before building the Panic Index, we first verified the core assumption:
**does market sentiment correlate with price deviation from trend across all major indices?**

The scatter plots below show Fear & Greed Index vs. price deviation from MA50
for all four indices. Color and size indicate VIX level.
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
**Observation**: All four indices show a consistent positive correlation —
extreme fear (bottom-left, red dots) clusters well below MA50, while extreme
greed (top-right, green dots) clusters above. This pattern holds across QQQ,
SMH, SPX, and DJI, motivating the construction of a composite sentiment-based
trading signal.

""")

st.divider()

st.markdown("""
### 💡Two Approaches to Signal Detection

The scatter plots confirm that extreme sentiment (low Fear & Greed + high VIX)
consistently corresponds to price dips below MA50. This raises the key question:

> **How do we systematically identify these extreme sentiment moments as trading signals?**

We compare two methods:

| | 💠 Threshold Strategy | ⚡ Peak Detection |
|---|---|---|
| **Method** | Fixed threshold (Panic > 64) | `scipy.find_peaks` |
| **Signal Type** | Absolute level | Relative local peak |
| **Advantage** | Simple, transparent, data-driven threshold | Adaptive, no fixed cutoff needed |
| **Limitation** | May miss peaks below threshold | Sensitive to parameter tuning |
| **Best for** | Identifying extreme panic regimes | Catching every significant spike |

👈 Select a strategy from the sidebar to explore.
""")