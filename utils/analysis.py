import pandas as pd

from .metrics import build_performance_metrics


def _correlation_or_na(paired, x_col, y_col, method):
    if len(paired) < 2:
        return float('nan')
    if paired[x_col].nunique() < 2 or paired[y_col].nunique() < 2:
        return float('nan')
    return paired[x_col].corr(paired[y_col], method=method)


def build_backtest_verdict(bt):
    metrics = build_performance_metrics(bt)
    strat_return = metrics['strategy_return']
    bnh_return = metrics['buyhold_return']
    strat_dd = metrics['strategy_max_drawdown']
    bnh_dd = metrics['buyhold_max_drawdown']

    strat_wins = sum([
        strat_return > bnh_return,
        metrics['strategy_sharpe'] > metrics['buyhold_sharpe'],
        strat_dd > bnh_dd,
    ])

    if strat_wins == 3:
        return "The strategy outperforms Buy & Hold on all three metrics."
    if strat_wins == 2:
        return "The strategy outperforms Buy & Hold on two of three metrics."
    if strat_dd > bnh_dd:
        return (
            f"Buy & Hold outperforms on absolute return and Sharpe ratio. "
            f"However, the strategy reduces maximum drawdown significantly "
            f"({strat_dd:.1f}% vs {bnh_dd:.1f}%), making it more suitable for risk-averse investors."
        )
    return "Buy & Hold outperforms the strategy on all metrics in this period."


def build_strategy_comparison_table(dff, target_col, buy_threshold,
                                    scipy_mask, rt_mask):
    horizon_labels = {1: '1 Day', 5: '1 Week', 21: '1 Month', 63: '3 Months'}
    threshold_mask = dff['panic_index'] > buy_threshold

    comparison_data = []
    for mask, label, horizons_list in [
        (threshold_mask, f"Threshold (>{buy_threshold})", [1, 5, 21, 63]),
        (scipy_mask, "scipy Peaks", [1, 5, 21, 63]),
        (rt_mask, "RealTime Peaks", [1, 5, 21, 63]),
    ]:
        for h in horizons_list:
            fwd = (dff[target_col].shift(-h) / dff[target_col] - 1) * 100
            returns = fwd[mask].dropna()
            if not returns.empty:
                comparison_data.append({
                    "Strategy": label,
                    "Horizon": horizon_labels[h],
                    "Signals": len(returns),
                    "Win Rate": f"{(returns > 0).mean():.1%}",
                    "Avg Ret": f"{returns.mean():.2f}%",
                    "Max": f"{returns.max():.2f}%",
                    "Min": f"{returns.min():.2f}%",
                })

    return pd.DataFrame(comparison_data)


def build_correlation_evidence_summary(
    market_data,
    markets,
    x_col='fear_greed_index',
    y_col='price_dev_pct',
):
    """Summarize descriptive correlation between sentiment and price deviation.

    Compares Fear & Greed Index with price deviation from MA50 for each market.
    The output is descriptive evidence only; correlation does not imply
    causation or future predictive performance.
    """
    rows = []

    for market_key, market_label in markets:
        dff = market_data[market_key]
        paired = dff[[x_col, y_col]].dropna()
        n_obs = len(paired)

        pearson = _correlation_or_na(paired, x_col, y_col, 'pearson')
        spearman = _correlation_or_na(paired, x_col, y_col, 'spearman')

        rows.append({
            "Market": market_label,
            "Pearson r": pearson,
            "Spearman ρ": spearman,
            "N": n_obs,
        })

    summary = pd.DataFrame(rows)
    summary["Pearson r"] = summary["Pearson r"].round(3)
    summary["Spearman ρ"] = summary["Spearman ρ"].round(3)
    return summary


def build_strategy_diagnostics(bt):
    """Build compact strategy diagnostics from an existing backtest result.

    These diagnostics explain exposure behavior rather than duplicating
    performance metrics shown in the Backtest Performance section.
    """
    position = bt['position'].dropna()
    average_exposure = position.mean() if not position.empty else float('nan')
    time_below_full = (position < 1.0).mean() if not position.empty else float('nan')

    return {
        "average_exposure": average_exposure,
        "time_below_full_exposure": time_below_full,
    }


def build_exposure_tradeoff_attribution(bt):
    """Build arithmetic attribution for the trade-off from reduced exposure.

    Attribution uses lagged effective exposure to match the backtest timing:
    strategy_return_t = daily_return_t * position_{t-1}. Returned attribution
    values are percentage points. They are arithmetic diagnostics and do not
    exactly reconcile to compounded total-return differences.
    """
    attribution = bt[['date_dt', 'position', 'daily_return']].copy()
    attribution['effective_exposure'] = attribution['position'].shift(1).fillna(0)
    attribution = attribution.dropna(
        subset=['date_dt', 'daily_return', 'effective_exposure']
    ).copy()

    if attribution.empty:
        return {
            "upside_drag": 0.0,
            "downside_cushion": 0.0,
            "net_exposure_tradeoff": 0.0,
            "largest_episode_start": None,
            "largest_episode_end": None,
            "largest_episode_average_exposure": None,
            "largest_episode_upside_drag": None,
            "largest_episode_downside_cushion": None,
        }

    underexposure = (1 - attribution['effective_exposure']).clip(lower=0)
    attribution['upside_drag'] = (
        attribution['daily_return'].clip(lower=0) * underexposure
    )
    attribution['downside_cushion'] = (
        (-attribution['daily_return']).clip(lower=0) * underexposure
    )
    attribution['is_underexposed'] = attribution['effective_exposure'] < 1

    upside_drag = attribution['upside_drag'].sum() * 100
    downside_cushion = attribution['downside_cushion'].sum() * 100
    largest_episode = _largest_underexposed_episode(attribution)

    return {
        "upside_drag": upside_drag,
        "downside_cushion": downside_cushion,
        "net_exposure_tradeoff": upside_drag - downside_cushion,
        "largest_episode_start": largest_episode.get("start"),
        "largest_episode_end": largest_episode.get("end"),
        "largest_episode_average_exposure": largest_episode.get("average_exposure"),
        "largest_episode_upside_drag": largest_episode.get("upside_drag"),
        "largest_episode_downside_cushion": largest_episode.get("downside_cushion"),
    }


def _largest_underexposed_episode(attribution):
    underexposed = attribution[attribution['is_underexposed']].copy()
    if underexposed.empty:
        return {}

    episode_id = attribution['is_underexposed'].ne(
        attribution['is_underexposed'].shift(fill_value=False)
    ).cumsum()
    underexposed['episode_id'] = episode_id[underexposed.index]

    episodes = underexposed.groupby('episode_id').agg(
        start=('date_dt', 'min'),
        end=('date_dt', 'max'),
        average_exposure=('effective_exposure', 'mean'),
        upside_drag=('upside_drag', 'sum'),
        downside_cushion=('downside_cushion', 'sum'),
    )
    episodes[['upside_drag', 'downside_cushion']] *= 100
    largest = episodes.sort_values(
        ['upside_drag', 'downside_cushion'],
        ascending=[False, True],
    ).iloc[0]

    return {
        "start": largest['start'],
        "end": largest['end'],
        "average_exposure": largest['average_exposure'],
        "upside_drag": largest['upside_drag'],
        "downside_cushion": largest['downside_cushion'],
    }
