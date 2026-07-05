import numpy as np


def prepare_panic_signal(dff, use_zscore):
    if use_zscore:
        roll_mean = dff['panic_index'].rolling(252).mean()
        roll_std = dff['panic_index'].rolling(252).std()
        dff['panic_signal'] = (
            (dff['panic_index'] - roll_mean) / roll_std.replace(0, np.nan)
        )
        return dff, "Rolling Z-Score (252-day)", "panic_signal"

    dff['panic_signal'] = dff['panic_index']
    return dff, "Composite Panic Index", "panic_signal"
