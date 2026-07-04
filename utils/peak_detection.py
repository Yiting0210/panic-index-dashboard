from scipy.signal import find_peaks

def detect_peaks_scipy(dff, prominence=10, distance=10, signal_col='panic_index'):
    """
    Detect local peaks in Panic Index using scipy.signal.find_peaks.
    Returns DataFrame of peak rows.
    """
    values = dff[signal_col].fillna(0).values
    peaks, _ = find_peaks(values, prominence=prominence, distance=distance)
    return dff.iloc[peaks].copy()


def detect_peaks_realtime(dff, entry_threshold=80, fall_back_pct=0.05,
                          signal_col='panic_index'):
    """
    Simulate real-time peak detection using trailing max + drawdown trigger.
    Causal algorithm — no lookahead bias.
    Returns DataFrame with buy signal dates.
    """
    signals = []
    current_max = 0
    in_watch_zone = False

    for i in range(len(dff)):
        val = dff[signal_col].iloc[i]

        # Enter watch zone when panic exceeds entry threshold
        if val >= entry_threshold:
            in_watch_zone = True

        if in_watch_zone:
            # Track local maximum
            if val > current_max:
                current_max = val

            # Trigger buy when value drops below trailing max by fall_back_pct
            trigger_level = current_max * (1 - fall_back_pct)
            if val < trigger_level and current_max > 0:
                signals.append(dff.index[i])
                # Reset state
                in_watch_zone = False
                current_max = 0

    return dff.loc[signals].copy()
