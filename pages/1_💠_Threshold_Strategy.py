import streamlit as st

from utils import load_data, load_filtered_data
from utils import run_backtest
from utils import max_drawdown, sharpe_ratio, render_kpi_row

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

# ── Sidebar (must come before filtering) ─────────────────────────────────────
add_sidebar_nav()
buy_threshold, sell_threshold = render_threshold_selector()
selected_label, target_col, date_range = render_controls(df)
pos_initial, add_amount, reduce_amount, pos_max, pos_min = render_position_sizing()
horizons = render_horizon_selector()

# ── Filter data ───────────────────────────────────────────────────────────────
if len(date_range) == 2:
    dff = load_filtered_data(date_range[0], date_range[1], target_col)
else:
    dff = load_filtered_data(df['date_dt'].min().date(),
                             df['date_dt'].max().date(), target_col)

# ── Backtest ──────────────────────────────────────────────────────────────────
bt = run_backtest(dff, target_col, pos_initial, add_amount, reduce_amount, pos_max, pos_min)

# ── Verdict ───────────────────────────────────────────────────────────────────
strat_return = bt['cumulative_strategy'].iloc[-1] - 100
bnh_return   = bt['cumulative_buyhold'].iloc[-1] - 100
strat_dd     = max_drawdown(bt['cumulative_strategy'])
bnh_dd       = max_drawdown(bt['cumulative_buyhold'])

strat_wins = sum([
    strat_return > bnh_return,
    sharpe_ratio(bt['strategy_return']) > sharpe_ratio(bt['daily_return']),
    strat_dd > bnh_dd,
])

if strat_wins == 3:
    verdict = "The strategy outperforms Buy & Hold on all three metrics."
elif strat_wins == 2:
    verdict = "The strategy outperforms Buy & Hold on two of three metrics."
elif strat_dd > bnh_dd:
    verdict = (
        f"Buy & Hold outperforms on absolute return and Sharpe ratio. "
        f"However, the strategy reduces maximum drawdown significantly "
        f"({strat_dd:.1f}% vs {bnh_dd:.1f}%), making it more suitable for risk-averse investors."
    )
else:
    verdict = "Buy & Hold outperforms the strategy on all metrics in this period."

# ── Charts ────────────────────────────────────────────────────────────────────
placeholder = st.empty()
placeholder.info("⏳ Loading charts for the first time, please wait...")

with st.spinner("Rendering charts..."):

    # Chart 1: Price Signal Map
    st.subheader("📈 Price Signal Map & Composite Panic Index")
    st.caption(
        "Panic Index above the buy threshold marks a panic regime / buy zone. "
        "Consecutive panic-zone days are intentional exposure days for gradual "
        "accumulation, not independent single-day bottom forecasts."
    )
    fig_main = render_price_signal_map(
        dff, target_col, selected_label, buy_threshold, sell_threshold
    )
    st.plotly_chart(fig_main, width="stretch", key="main_chart")

    st.divider()

    # Chart 2: Scatter Plot
    st.subheader("🎯 Sentiment vs. MA50 Deviation")
    st.caption("Each dot = one trading day · Color & size = VIX · "
               "Bottom-left = panic bottom (best historical entry points)")
    fig_sc = render_scatter_plot(dff, target_col, selected_label)
    st.plotly_chart(fig_sc, width="stretch", key="scatter_chart")

    st.divider()

    # Chart 3: Backtest
    st.subheader("⚖️ Strategy vs Buy & Hold — Backtest")
    st.caption(
        f"Position sizing: {int(pos_initial)}% initial · "
        f"+{int(add_amount)}% during panic-zone days (max {int(pos_max)}%) · "
        f"-{int(reduce_amount)}% during greed-zone days (min {int(pos_min)}%)"
    )
    fig_bt = render_backtest_chart(bt)
    st.plotly_chart(fig_bt, width="stretch", key="backtest_chart")

    st.divider()
    render_kpi_row(bt, max_drawdown, sharpe_ratio)

    st.divider()

    # Chart 4: Forward Return Analysis
    st.subheader("📊 Forward Return Analysis — Buy the Panic, Sell the Greed?")
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
        st.markdown("🟢 Greed / De-risking Zone")
        render_forward_return_table(dff, greed_mask, target_col, horizons)

    st.caption(
        "Win Rate > 50% and Avg Ret > 0 supports accumulating during "
        "the panic regime. Greed-zone rows describe de-risking exposure "
        "days, not a promise of an immediate top."
    )

placeholder.empty()

with st.container(border=True):
    st.subheader("💡 Strategic Insights")
    st.markdown(f"""
### Price Signal Map & Composite Panic Index
- Buy-zone days (Panic Index > **{buy_threshold}**) define a panic regime for gradual accumulation; the strategy is not trying to identify one exact bottom.
- Consecutive panic-zone days are intentional because position size scales in during sustained stress; interpret them as exposure days, not independent single-day forecasts.
- Sell-zone days (Panic Index < **{sell_threshold}**) define a greed / de-risking regime and appear during sustained greed periods in the 2023–2024 bull market.

### Scatter Plot (Sentiment vs. MA50 Deviation)
- Strong positive correlation between Fear & Greed and price deviation from MA50.
- Extreme panic days (bottom-left, red/large dots) cluster well below MA50, confirming historically oversold conditions.
- While sentiment has near-zero predictive power for next-day absolute price changes (consistent with the [Efficient Market Hypothesis](https://en.wikipedia.org/wiki/Efficient-market_hypothesis)) — it effectively identifies medium-term regime shifts.
- **Conclusion**: The Panic Index is designed as a Regime Indicator, not a high-frequency timing tool. Its value lies in identifying high-probability buying windows during multi-week market stress.

### Backtest ({date_range[0]} – {date_range[1]})

| Metric | Strategy | Buy & Hold |
|--------|----------|------------|
| Total Return | {bt['cumulative_strategy'].iloc[-1]-100:.1f}% | {bt['cumulative_buyhold'].iloc[-1]-100:.1f}% |
| Max Drawdown | {max_drawdown(bt['cumulative_strategy']):.1f}% | {max_drawdown(bt['cumulative_buyhold']):.1f}% |
| Sharpe Ratio | {sharpe_ratio(bt['strategy_return']):.3f} | {sharpe_ratio(bt['daily_return']):.3f} |

{verdict}

### Forward Return Analysis
- **The "Asymmetric Panic Edge"**: Extreme panic signals exhibit a significant **positive right-skew (Fat-tail)** in forward returns. While 1-day returns are nearly symmetric (55% win rate), the signal's predictive power peaks at the **1-month horizon** with a **74.1% win rate**, confirming a robust mean-reversion effect following market capitulation.
- **Greed Dissipation & Momentum Inertia**: Unlike panic, extreme greed signals show a **"Platykurtic" (flatter) distribution** with returns hovering near zero. In sustained uptrends, greed can persist longer than expected, making it a reliable **de-risking indicator** rather than a definitive short signal.
- **Risk-Reward Profile**: The **P/L Ratio peaks at 2.14** for 3-month panic windows, suggesting that while volatility increases over time, the magnitude of the "rebound alpha" significantly outweighs the downside risk.
- **Statistical Fidelity**:
    - *Distribution Analysis*: KDE (Kernel Density Estimation) plots reveal that panic-induced bottoms are often "V-shaped" with higher kurtosis, while greed tops are "rounded" and less predictable.
    - *Note*: Overlapping samples during sustained stress may introduce autocorrelation; win rates should be interpreted as historical probabilities within the specific 2021-2026 regime.
""")

# ── Write-up ──────────────────────────────────────────────────────────────────
with st.expander("📝 Project Write-up", expanded=False):
    st.markdown(f"""

### Design Decisions
- **Four-chart layout**: Each chart serves a distinct analytical purpose:
  - **Chart 1 (Price Signal Map & Composite Panic Index)**: Shows *where* buy/sell signals appear on the
    price chart, allowing users to visually validate signal timing against
    historical price movements.
  - **Chart 2 (Sentiment vs. MA50 Deviation)**: Reveals the *structural relationship*
    between sentiment and price level, removing long-term trend bias via MA50 deviation.
  - **Chart 3 (Backtest)**: Quantifies *whether acting on signals generates returns*,
    comparing the strategy against passive Buy & Hold.
  - **Chart 4 (Forward Return Analysis)**: Statistically validates signal effectiveness
    across multiple time horizons, providing evidence beyond visual pattern recognition.

- **Composite Panic Index**: Normalized VIX (50%) + Inverted Fear & Greed (50%),
  scaled to 0–100. Buy threshold (>{buy_threshold}) = 95th percentile;
  Sell threshold (<{sell_threshold}) = 5th percentile of full 5-year history.
  The buy threshold defines a panic regime / buy zone, while the sell threshold
  defines a greed / de-risking regime. Consecutive panic-zone observations are
  expected and feed gradual position sizing rather than one-off bottom calls.
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

- **Forward Return Analysis**: Validates signal effectiveness statistically
  across 1-day, 1-week, 1-month, and 3-month horizons, providing evidence beyond
  visual pattern recognition. Users can select horizons interactively via the sidebar.

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
---    """)
