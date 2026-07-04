import pandas as pd
import numpy as np



def run_backtest(dff, target_col, pos_initial=50, add_amount=20,
                 reduce_amount=20, pos_max=100, pos_min=0, buy_threshold=64, sell_threshold=15):
    bt = dff[['date_dt', target_col, 'panic_index']].dropna().copy().reset_index(drop=True)

    position = pos_initial / 100  # 转成小数
    positions = []

    for _, row in bt.iterrows():
        if row['panic_index'] > buy_threshold:
            position = min(position + add_amount/100, pos_max/100)
        elif row['panic_index'] < sell_threshold:
            position = max(position - reduce_amount/100, pos_min/100)
        positions.append(position)

    bt['position']            = positions
    bt['daily_return']        = bt[target_col].pct_change(fill_method=None)
    bt['strategy_return'] = bt['daily_return'] * bt['position'].shift(1).fillna(0)
    bt['cumulative_strategy'] = (1 + bt['strategy_return'].fillna(0)).cumprod() * 100
    bt['cumulative_buyhold']  = (1 + bt['daily_return'].fillna(0)).cumprod() * 100
    return bt

def run_backtest_with_signals(dff, target_col, signal_mask,
                               pos_initial=50, add_amount=20,
                               reduce_amount=10, pos_max=100, pos_min=0):
    """
    Backtest using a precomputed signal mask instead of threshold.
    signal_mask: boolean Series where True = buy signal day
    """
    bt = dff[['date_dt', target_col]].dropna().copy().reset_index(drop=True)
    signal_mask = signal_mask.reindex(bt.index, fill_value=False)

    position  = pos_initial / 100
    positions = []

    for i, row in bt.iterrows():
        if signal_mask.iloc[i]:
            position = min(position + add_amount / 100, pos_max / 100)
        positions.append(position)

    bt['position']            = positions
    bt['daily_return']        = bt[target_col].pct_change(fill_method=None)
    bt['strategy_return']     = bt['daily_return'] * bt['position'].shift(1).fillna(0)
    bt['cumulative_strategy'] = (1 + bt['strategy_return'].fillna(0)).cumprod() * 100
    bt['cumulative_buyhold']  = (1 + bt['daily_return'].fillna(0)).cumprod() * 100
    return bt
