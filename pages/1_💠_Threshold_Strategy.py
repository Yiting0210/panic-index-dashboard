import streamlit as st
import pandas as pd

from utils import load_data, load_filtered_data
from utils import run_backtest
from utils import build_performance_metrics, render_kpi_row
from utils import build_threshold_research_summary
from utils import build_strategy_diagnostics
from utils import build_exposure_tradeoff_attribution

from utils import (add_sidebar_nav, render_controls,
                   render_position_sizing, render_horizon_selector,
                   render_threshold_selector)
from utils import (render_price_signal_map, render_scatter_plot,
                   render_backtest_chart,
                   render_forward_return_histogram, render_forward_return_table)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(layout="wide", page_title="Threshold Strategy")


# ── Load data ─────────────────────────────────────────────────────────────────
df = load_data()

# ── Header ────────────────────────────────────────────────────────────────────
st.title("Market Sentiment & Panic Index Dashboard")
st.markdown(
    "Exploring Market Asymmetry: Can a Composite Panic Index (VIX + CNN Fear & Greed) "
    "Identify Tactical Entry and Exit Opportunities Across Major Indices and the "
    "Semiconductor Sector (2021–2026)?"
)
st.caption(
    "The page focuses on the asymmetric research question: panic-side signals "
    "are studied primarily as accumulation candidates, while greed-side signals "
    "are treated as exploratory risk-warning / de-risking candidates rather "
    "than equally reliable immediate exit signals."
)

# ── Sidebar (must come before filtering) ─────────────────────────────────────
add_sidebar_nav()
buy_threshold, sell_threshold = render_threshold_selector()
selected_label, target_col, date_range = render_controls(df)
pos_initial, add_amount, reduce_amount, pos_max, pos_min = render_position_sizing()
horizons = render_horizon_selector()

min_date = df['date_dt'].min().date()
max_date = df['date_dt'].max().date()
if len(date_range) == 2:
    start_date, end_date = date_range
elif len(date_range) == 1:
    start_date, end_date = date_range[0], max_date
else:
    start_date, end_date = min_date, max_date

# ── Filter data ───────────────────────────────────────────────────────────────
dff = load_filtered_data(start_date, end_date, target_col)

# ── Backtest ──────────────────────────────────────────────────────────────────
bt = run_backtest(
    dff,
    target_col,
    pos_initial,
    add_amount,
    reduce_amount,
    pos_max,
    pos_min,
    buy_threshold,
    sell_threshold,
)
diagnostics = build_strategy_diagnostics(bt)
attribution = build_exposure_tradeoff_attribution(bt)
performance_metrics = build_performance_metrics(bt)

# ── Verdict ───────────────────────────────────────────────────────────────────
strat_return = performance_metrics['strategy_return']
bnh_return = performance_metrics['buyhold_return']
strat_dd = performance_metrics['strategy_max_drawdown']
bnh_dd = performance_metrics['buyhold_max_drawdown']

strat_wins = sum([
    strat_return > bnh_return,
    performance_metrics['strategy_sharpe'] > performance_metrics['buyhold_sharpe'],
    strat_dd > bnh_dd,
])

if strat_wins == 3:
    verdict = "In this sample, the strategy outperforms Buy & Hold on all three metrics."
elif strat_wins == 2:
    verdict = "In this sample, the strategy outperforms Buy & Hold on two of three metrics."
elif strat_dd > bnh_dd:
    verdict = (
        f"Buy & Hold outperforms on absolute return and Sharpe ratio. "
        f"However, the strategy shows a smaller maximum drawdown in this sample "
        f"({strat_dd:.1f}% vs {bnh_dd:.1f}%), making it more suitable for risk-averse investors."
    )
else:
    verdict = "Buy & Hold outperforms the strategy on all metrics in this sample."

with st.container(border=True):
    st.subheader("🔎 Research Summary")
    st.markdown(
        build_threshold_research_summary(
            dff,
            bt,
            target_col,
            selected_label,
            buy_threshold,
            sell_threshold,
            diagnostics,
            attribution,
        )
    )
    st.caption(
        "Deterministic interpretation of historical dashboard outputs; "
        "not investment advice or a live trading recommendation."
    )

with st.container(border=True):
    st.subheader("🔬 Strategy Diagnostics")
    st.caption(
        "Exposure diagnostics test the trade-off created by greed-side "
        "risk-warning / de-risking rules. Lower exposure is not automatically "
        "bad; it can cushion declines but may create prolonged underexposure "
        "during sustained uptrends."
    )
    col_exp, col_below = st.columns(2)
    col_exp.metric(
        "Average Exposure",
        f"{diagnostics['average_exposure'] * 100:.1f}%",
        help="Mean portfolio exposure over the selected backtest period.",
    )
    col_below.metric(
        "Time Below Full Exposure",
        f"{diagnostics['time_below_full_exposure'] * 100:.1f}%",
        help="Share of observations where strategy exposure was below 100%.",
    )
    st.markdown("**Exposure Trade-off Attribution**")

    episode_start = attribution['largest_episode_start']
    episode_end = attribution['largest_episode_end']
    if episode_start is None or episode_end is None:
        episode_value = "N/A"
        episode_interpretation = "No underexposed episode in selected period"
    else:
        episode_value = (
            f"{episode_start:%Y-%m-%d} to {episode_end:%Y-%m-%d}, "
            f"{attribution['largest_episode_upside_drag']:.1f} pp"
        )
        episode_interpretation = (
            "Largest continuous low-exposure interval by upside drag"
        )

    st.dataframe(
        pd.DataFrame([
            {
                "Attribution Item": "Underexposed Upside Drag",
                "Value": f"{attribution['upside_drag']:.1f} pp",
                "Interpretation": "Positive market return not fully captured",
            },
            {
                "Attribution Item": "Underexposed Downside Cushion",
                "Value": f"{attribution['downside_cushion']:.1f} pp",
                "Interpretation": "Negative market return avoided",
            },
            {
                "Attribution Item": "Net Arithmetic Trade-off",
                "Value": f"{attribution['net_exposure_tradeoff']:.1f} pp",
                "Interpretation": "Upside drag minus downside cushion",
            },
            {
                "Attribution Item": "Largest Underexposed Upside Episode",
                "Value": episode_value,
                "Interpretation": episode_interpretation,
            },
        ]),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Arithmetic attribution over dashboard observation periods. Uses "
        "lagged effective exposure to match backtest timing and does not "
        "exactly reconcile to compounded total-return differences."
    )

# ── Charts ────────────────────────────────────────────────────────────────────
placeholder = st.empty()
placeholder.info("⏳ Loading charts for the first time, please wait...")

with st.spinner("Rendering charts..."):

    # Chart 1: Price Signal Map
    st.subheader("📈 Price Signal Map & Composite Panic Index")
    st.caption(
        "Panic Index above the buy threshold marks a panic regime / buy zone. "
        "Consecutive panic-zone days are intentional exposure days for gradual "
        "accumulation, not independent single-day bottom forecasts. Greed-side "
        "thresholds are evaluated as risk-warning / de-risking candidates, not "
        "as symmetric sell signals."
    )
    fig_main = render_price_signal_map(
        dff, target_col, selected_label, buy_threshold, sell_threshold
    )
    st.plotly_chart(fig_main, width="stretch", key="main_chart")

    st.divider()

    # Chart 2: Scatter Plot
    st.subheader("🎯 Sentiment vs. MA50 Deviation")
    st.caption("Each dot = one trading day · Color & size = VIX · "
               "Bottom-left = higher-stress observations below trend")
    fig_sc = render_scatter_plot(dff, target_col, selected_label)
    st.plotly_chart(fig_sc, width="stretch", key="scatter_chart")

    st.divider()

    # Chart 3: Backtest
    st.subheader("⚖️ Strategy vs Buy & Hold — Backtest")
    st.caption(
        f"Position sizing: {int(pos_initial)}% initial · "
        f"+{int(add_amount)}% during panic-zone days (max {int(pos_max)}%) · "
        f"-{int(reduce_amount)}% during greed / risk-warning days "
        f"(min {int(pos_min)}%)"
    )
    fig_bt = render_backtest_chart(bt)
    st.plotly_chart(fig_bt, width="stretch", key="backtest_chart")

    st.divider()
    st.subheader("Backtest Performance")
    render_kpi_row(bt, performance_metrics)
    st.caption(
        "Sharpe uses arithmetic period returns, excludes missing returns, "
        "assumes a 0% annual risk-free rate, and annualizes using the observed "
        "backtest rows per elapsed calendar year. Calmar uses annualized "
        "return over the selected date span divided by absolute maximum "
        "drawdown."
    )

    st.divider()

    # Chart 4: Forward Return Analysis
    st.subheader(
        "📊 Forward Return Analysis — Panic Accumulation vs Greed Risk Warning"
    )
    panic_mask = dff['panic_index'] > buy_threshold
    greed_mask = dff['panic_index'] < sell_threshold

    col_chart, col_table = st.columns([2, 1])
    with col_chart:
        st.caption(
            "Probability Distribution of Returns After Panic-Zone Exposure Days"
        )
        fig_hist = render_forward_return_histogram(dff, panic_mask, target_col, horizons)
        st.plotly_chart(fig_hist, width="stretch", key="hist_panic")
    with col_table:
        st.caption("**Statistical Edge Summary**")
        st.markdown("🔴 Panic Regime / Buy Zone")
        render_forward_return_table(dff, panic_mask, target_col, horizons)
        st.markdown("🟢 Greed / Risk-Warning Zone")
        render_forward_return_table(dff, greed_mask, target_col, horizons)

    st.caption(
        "Win Rate > 50% and Avg Ret > 0 supports accumulating during "
        "the panic regime. Greed-zone rows describe exploratory risk-warning "
        "/ de-risking exposure days, not a promise of an immediate top or a "
        "symmetric sell rule."
    )

placeholder.empty()

with st.container(border=True):
    st.subheader("💡 Strategic Insights")
    st.markdown(f"""
### Price Signal Map & Composite Panic Index
- Buy-zone days (Panic Index > **{buy_threshold}**) define a panic regime for gradual accumulation; the strategy is not trying to identify one exact bottom.
- Consecutive panic-zone days are intentional because position size scales in during sustained stress; interpret them as exposure days, not independent single-day forecasts.
- Greed / risk-warning days (Panic Index < **{sell_threshold}**) are exploratory de-risking candidates. In this sample, greed can persist during bull markets, so it should not be interpreted as an equally reliable immediate exit signal.

### Scatter Plot (Sentiment vs. MA50 Deviation)
- Fear & Greed has been historically associated with price deviation from MA50 in this sample.
- Higher-stress observations (bottom-left, red/large dots) often appear below MA50, consistent with historically oversold regimes.
- While sentiment has near-zero next-day association with absolute price changes (consistent with the [Efficient Market Hypothesis](https://en.wikipedia.org/wiki/Efficient-market_hypothesis)), it may still be useful for descriptive regime identification.
- **Conclusion**: The Panic Index is designed as a regime indicator, not a high-frequency timing tool. Its value here is descriptive evidence for studying market stress windows, not guaranteed alpha.

### Backtest ({start_date} – {end_date})

| Metric | Strategy | Buy & Hold |
|--------|----------|------------|
| Total Return | {performance_metrics['strategy_return']:.1f}% | {performance_metrics['buyhold_return']:.1f}% |
| Max Drawdown | {performance_metrics['strategy_max_drawdown']:.1f}% | {performance_metrics['buyhold_max_drawdown']:.1f}% |
| Sharpe Ratio | {performance_metrics['strategy_sharpe']:.3f} | {performance_metrics['buyhold_sharpe']:.3f} |
| Calmar Ratio | {performance_metrics['strategy_calmar']:.3f} | {performance_metrics['buyhold_calmar']:.3f} |

{verdict}

### Forward Return Analysis
- **Asymmetric Historical Pattern**: Extreme panic regimes are historically associated with more favorable forward-return distributions in this sample. The 1-month horizon shows a higher observed win rate than shorter horizons, but this should not be interpreted as guaranteed alpha or a live trading claim.
- **Greed Persistence & Momentum Inertia**: Unlike panic, extreme greed regimes can persist during sustained uptrends. In this sample, greed appears less reliable as an immediate exit signal and is better framed as a risk-awareness context or exploratory de-risking candidate.
- **Risk-Reward Profile**: The P/L ratio is higher for some longer panic windows in this sample, suggesting that rebound magnitude has historically outweighed downside in selected periods. This is descriptive evidence, not proof of persistent alpha.
- **Methodological Fidelity**:
    - *Distribution Analysis*: KDE (Kernel Density Estimation) plots help describe the shape of historical return distributions after panic and greed regimes.
    - *Note*: Overlapping samples during sustained stress may introduce autocorrelation; win rates should be interpreted as historical probabilities within the specific 2021-2026 regime.
""")

# ── Write-up ──────────────────────────────────────────────────────────────────
with st.expander("📝 Project Write-up", expanded=False):
    st.markdown(f"""

### Design Decisions
- **Four-chart layout**: Each chart serves a distinct analytical purpose:
  - **Chart 1 (Price Signal Map & Composite Panic Index)**: Shows *where* panic and greed threshold regimes appear on the
    price chart, allowing users to visually validate regime timing against
    historical price movements.
  - **Chart 2 (Sentiment vs. MA50 Deviation)**: Reveals the *structural relationship*
    between sentiment and price level, removing long-term trend bias via MA50 deviation.
  - **Chart 3 (Backtest)**: Quantifies *whether acting on signals generates returns*,
    comparing the strategy against passive Buy & Hold.
  - **Chart 4 (Forward Return Analysis)**: Summarizes descriptive forward-return
    behavior across multiple time horizons, providing evidence beyond visual pattern recognition.

- **Composite Panic Index**: Normalized VIX (50%) + Inverted Fear & Greed (50%),
  scaled to 0–100. Buy threshold (>{buy_threshold}) = 95th percentile;
  Greed / risk-warning threshold (<{sell_threshold}) = 5th percentile of full 5-year history.
  The buy threshold defines a panic regime / buy zone, while the sell threshold
  defines an exploratory greed / risk-warning regime. Consecutive panic-zone observations are
  expected and feed gradual position sizing rather than one-off bottom calls;
  greed-side de-risking is evaluated as a less symmetric, more tentative rule.
  Thresholds are fixed — consistent with real-world quant strategy development
  where thresholds are calibrated on historical data and held constant during deployment.

- **Removed standalone VIX and F&G charts**: Both indicators are already encoded
  in the Panic Index. Keeping them separately would be redundant.

- **Sharpe Ratio & Maximum Drawdown**: Used alongside total return to evaluate
  strategy quality. Sharpe measures risk-adjusted return (excess return per unit
  of volatility); Max Drawdown measures the largest peak-to-trough decline —
  critical for risk-averse investors.

- **Scatter plot (MA50 deviation)**: Uses price deviation from MA50 instead of
  absolute price to remove long-term trend bias, revealing the true relationship
  between sentiment and relative price level. MA50 chosen as the medium-term
  benchmark widely used by institutional investors.

- **Forward Return Analysis**: Summarizes historical forward-return behavior
  across 1-day, 1-week, 1-month, and 3-month horizons, providing descriptive
  evidence beyond visual pattern recognition. Users can select horizons interactively via the sidebar.

- **Shared x-axis (Chart 1 + 2)**: Synchronized hover and zoom lets users trace
  any Panic Index spike directly to its corresponding price movement.

- **Date range filter**: All charts and backtest update simultaneously, enabling
  period-specific analysis (e.g., isolating the 2022 bear market or 2025 tariff shock).

- **Interactive position sizing**: Users can set initial position, add/reduce
  amounts per signal, and position limits — backtest updates in real time.

---

### Data Sources
- **Fear & Greed Index (2021–2026)**:
  [MacroMicro](https://sc.macromicro.me/charts/50108/cnn-fear-and-greed),
  [CNN](https://www.cnn.com/markets/fear-and-greed)
- **Price data**: Yahoo Finance via `yfinance` — QQQ, SMH, SPX, DJI, VIX
  daily adjusted closing prices.
- **Missing values**: Market holidays (e.g., Christmas, Thanksgiving, Good Friday,
  Easter) have no trading data and are handled via linear interpolation for
  chart continuity.

---

### References
- Shneiderman, B. (1994). Dynamic queries for visual information seeking.
  *IEEE Software*, 11(6), 70–77.
- Chart design inspired by standard financial dashboard conventions
  (TradingView, Bloomberg Terminal layout).
- Efficient Market Hypothesis. https://en.wikipedia.org/wiki/Efficient-market_hypothesis
- Buy the Panic. https://www.investopedia.com/terms/p/panicbuying.asp
---  """)
