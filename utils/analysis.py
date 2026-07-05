import pandas as pd

from .metrics import max_drawdown, sharpe_ratio


def build_backtest_verdict(bt):
    strat_return = bt['cumulative_strategy'].iloc[-1] - 100
    bnh_return = bt['cumulative_buyhold'].iloc[-1] - 100
    strat_dd = max_drawdown(bt['cumulative_strategy'])
    bnh_dd = max_drawdown(bt['cumulative_buyhold'])

    strat_wins = sum([
        strat_return > bnh_return,
        sharpe_ratio(bt['strategy_return']) > sharpe_ratio(bt['daily_return']),
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
