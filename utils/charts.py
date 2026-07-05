import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.stats import gaussian_kde
import streamlit as st
import pandas as pd
import numpy as np


def build_signal_lines(signal_dates, color):
    shapes = []
    for d in signal_dates:
        shapes.append(dict(
            type="line", x0=d, x1=d, y0=0, y1=1,
            xref="x", yref="y domain",
            line=dict(color=color, width=1.5),
        ))
    return shapes


def build_watch_zone_shapes(dff, entry_threshold):
    shapes = []
    watch_zone_dates = dff[dff['panic_signal'] >= entry_threshold]['date_dt']
    for d in watch_zone_dates:
        shapes.append(dict(
            type="rect",
            x0=d, x1=d,
            y0=entry_threshold, y1=dff['panic_signal'].max(),
            xref="x2", yref="y2",
            fillcolor="rgba(249,115,22,0.05)",
            line_width=0,
        ))
    return shapes


def add_price_traces(fig, dff, target_col):
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff[target_col],
                             name="Price", line=dict(color='black', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma50'],
                             name="MA50", line=dict(color='orange', dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma200'],
                             name="MA200", line=dict(color='blue', dash='dot', width=1.5)), row=1, col=1)


def add_panic_signal_trace(fig, dff, signal_label):
    fig.add_trace(go.Scatter(
        x=dff['date_dt'], y=dff['panic_signal'],
        name=signal_label,
        line=dict(color='#aaaaaa', width=1),
        fill='tozeroy', fillcolor='rgba(170,170,170,0.1)'
    ), row=2, col=1)


def add_peak_markers(fig, peaks, marker_name, marker_color):
    fig.add_trace(go.Scatter(
        x=peaks['date_dt'], y=peaks['panic_signal'],
        mode='markers', name=marker_name,
        marker=dict(color=marker_color, size=10, symbol='triangle-down')
    ), row=2, col=1)


def apply_peak_chart_layout(fig, signal_label):
    fig.update_layout(
        height=700, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=80, b=40, l=60, r=40)
    )
    fig.update_xaxes(tickformat="%b %Y", nticks=15, tickangle=45,
                     hoverformat="%b %d, %Y")
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text=signal_label, row=2, col=1)


def render_scatter_plot(dff, target_col, selected_label):
    """Sentiment vs MA50 Deviation scatter plot."""
    vix_min = float(dff['vix'].min())
    vix_max = float(dff['vix'].max())

    fig = go.Figure(go.Scatter(
        x=dff['fear_greed_index'],
        y=dff['price_dev_pct'],
        mode='markers',
        marker=dict(
            color=dff['vix'],
            line=dict(width=0.5, color='DarkSlateGrey'),
            colorscale='RdYlGn_r',
            cmin=vix_min, cmax=vix_max,
            size=dff['vix'].apply(
                lambda x: 6 + (x - vix_min) / max(vix_max - vix_min, 1) * 14
            ),
            showscale=True,
            colorbar=dict(title="VIX")
        ),
        text=dff['date_dt'].dt.strftime('%b %d, %Y'),
        customdata=dff['vix'],
        hovertemplate=(
            "Date: %{text}<br>"
            "Fear & Greed: %{x:.1f}<br>"
            "Price Dev from MA50: %{y:.2f}%<br>"
            "VIX: %{customdata:.2f}"
            "<extra></extra>"
        )
    ))
    fig.update_layout(
        xaxis_title="Fear & Greed Index  (0 = Extreme Fear  →  100 = Extreme Greed)",
        yaxis_title=f"{selected_label} Deviation from MA50 (%)",
        template="plotly_white", height=430,
        margin=dict(t=20, b=60, l=60, r=40),
        shapes=[dict(type='line', y0=0, y1=0, x0=0, x1=100,
                    line=dict(color='gray', dash='dash'))]
    )
    return fig

def render_backtest_chart(bt):
    """Render cumulative return comparison chart."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=bt['date_dt'], y=bt['cumulative_strategy'],
                             name="Panic Index Strategy",
                             line=dict(color='#EF553B', width=2)))
    fig.add_trace(go.Scatter(x=bt['date_dt'], y=bt['cumulative_buyhold'],
                             name="Buy & Hold",
                             line=dict(color='black', width=2, dash='dot')))
    fig.update_layout(
        height=380, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=40, b=40, l=60, r=40),
        yaxis_title="Cumulative Return (base = 100)"
    )
    fig.update_xaxes(tickformat="%b %Y", nticks=15, tickangle=45,
                     hoverformat="%b %d, %Y")
    return fig

def render_price_signal_map(dff, target_col, selected_label, buy_threshold, sell_threshold):
    """Render price chart with buy/sell signal overlays and Panic Index subplot."""
    buy_dates  = dff[dff['panic_index'] > buy_threshold]['date_dt']
    sell_dates = dff[dff['panic_index'] < sell_threshold]['date_dt']

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            f"Price & MA50/MA200 — {selected_label}",
            "Composite Panic Index  (VIX 50% + Inverted Fear & Greed 50%)"
        ),
        row_heights=[0.6, 0.4]
    )

    # Buy/sell signal vertical lines
    shapes = []
    for d in buy_dates:
        shapes.append(dict(
            type="line", x0=d, x1=d, y0=0, y1=1,
            xref="x", yref="y domain",
            line=dict(color="rgba(239,85,59,0.3)", width=1.5),
        ))
    for d in sell_dates:
        shapes.append(dict(
            type="line", x0=d, x1=d, y0=0, y1=1,
            xref="x", yref="y domain",
            line=dict(color="rgba(0,204,150,0.3)", width=1.5),
        ))
    fig.update_layout(shapes=shapes)

    # Row 1: Price + MA lines
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff[target_col], name="Price",
                             line=dict(color='black', width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma50'], name="MA50",
                             line=dict(color='orange', dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=dff['date_dt'], y=dff['ma200'], name="MA200",
                             line=dict(color='blue', dash='dot', width=1.5)), row=1, col=1)

    # Row 2: Panic Index area
    fig.add_trace(go.Scatter(
        x=dff['date_dt'], y=dff['panic_index'],
        name="Panic Index",
        line=dict(color='#aaaaaa', width=1),
        fill='tozeroy', fillcolor='rgba(170,170,170,0.1)'
    ), row=2, col=1)

    # Buy/sell signal markers on Panic Index
    buy_df  = dff[dff['panic_index'] > buy_threshold]
    sell_df = dff[dff['panic_index'] < sell_threshold]
    fig.add_trace(go.Scatter(
        x=buy_df['date_dt'], y=buy_df['panic_index'],
        mode='markers', name="Extreme Panic (Buy)",
        marker=dict(color='#EF553B', size=8, symbol='circle')
    ), row=2, col=1)
    fig.add_trace(go.Scatter(
        x=sell_df['date_dt'], y=sell_df['panic_index'],
        mode='markers', name="Extreme Greed (Sell)",
        marker=dict(color='#00CC96', size=8, symbol='circle')
    ), row=2, col=1)

    fig.add_hline(y=buy_threshold,  line_color="red",   line_dash="dash",
                  annotation_text="Buy Zone (95th pct)",  row=2, col=1)
    fig.add_hline(y=sell_threshold, line_color="green", line_dash="dash",
                  annotation_text="Sell Zone (5th pct)", row=2, col=1)

    fig.update_layout(
        height=750, hovermode="x unified", template="plotly_white",
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=80, b=40, l=60, r=40)
    )
    fig.update_xaxes(tickformat="%b %Y", nticks=15, tickangle=45)
    fig.update_xaxes(hoverformat="%b %d, %Y")
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Panic Index (0-100)", row=2, col=1)
    return fig

def render_forward_return_histogram(dff, signal_mask, target_col, horizons=[1, 5, 21, 63]):
    """
    Render overlapping histogram of forward return distributions after signal.
    """
    horizon_labels = {1: '1 Day', 5: '1 Week', 21: '1 Month', 63: '3 Months'}
    colors = {
        '1 Day':    '#636EFA',
        '1 Week':   '#00CC96',
        '1 Month':  '#EF553B',
        '3 Months': '#FFA15A',
    }
    fig = go.Figure()
    for h, label in horizon_labels.items():
        if h not in horizons:
            continue
        fwd = (dff[target_col].shift(-h) / dff[target_col] - 1) * 100
        data = fwd[signal_mask].dropna()
        color = colors.get(label, '#636EFA')

        #  Histogram
        fig.add_trace(go.Histogram(
            x=data, name=label, opacity=0.6, nbinsx=25,
            marker_color=color,
            histnorm='probability density'

        ))

        # KDE line
        if len(data) > 5:
            kde = gaussian_kde(data)
            x_range = np.linspace(data.min(), data.max(), 200)
            y_kde = kde(x_range)
            # Scale KDE to match histogram height
            bin_width = (data.max() - data.min()) / 25
            y_scaled = y_kde * len(data) * bin_width
            fig.add_trace(go.Scatter(
                x=x_range, y=y_scaled,
                mode='lines',
                name=f"{label} (KDE)",
                line=dict(color=color, width=2),
                showlegend=False,
            ))
    fig.add_vline(x=0, line_dash="dash", line_color="gray",
                  annotation_text="Break-even", annotation_position="top right")
    fig.update_layout(
        barmode='overlay',
        xaxis_title="Forward Return (%)",
        yaxis_title="Probability Density",
        template="plotly_white",
        height=380,
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right"),
        margin=dict(t=60, b=40, l=40, r=40)
    )
    return fig


def render_forward_return_table(dff, signal_mask, target_col, horizons=[1, 5, 21, 63]):
    """
    Render statistical edge summary table for a given signal mask.
    Returns a styled DataFrame.
    """

    horizon_labels = {1: '1 Day', 5: '1 Week', 21: '1 Month', 63: '3 Months'}
    results = []
    for h in horizons:
        fwd_returns = (dff[target_col].shift(-h) / dff[target_col] - 1) * 100
        signal_returns = fwd_returns[signal_mask].dropna()
        avg_win = signal_returns[signal_returns > 0].mean()
        avg_loss = abs(signal_returns[signal_returns < 0].mean())
        pl_ratio = avg_win / avg_loss if avg_loss != 0 else 0
        if not signal_returns.empty:
            results.append({
                "Horizon":  horizon_labels.get(h, f"T+{h}"),
                "Count":    len(signal_returns),
                "Win Rate": f"{(signal_returns > 0).mean():.1%}",
                "Avg Ret":  f"{signal_returns.mean():.2f}%",
                "Max":      f"{signal_returns.max():.2f}%",
                "Min":      f"{signal_returns.min():.2f}%",
                "P/L Ratio": f"{pl_ratio:.2f}",
            })
    df = pd.DataFrame(results)
    st.dataframe(df, width="stretch", hide_index=True)
