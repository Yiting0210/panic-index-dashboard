import pandas as pd


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
