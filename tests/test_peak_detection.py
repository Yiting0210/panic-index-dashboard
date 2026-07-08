import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils.peak_detection import detect_peaks_realtime


def test_realtime_detector_triggers_only_after_fallback_from_local_max():
    dff = pd.DataFrame({
        'date_dt': pd.date_range('2022-01-01', periods=7, freq='B'),
        'panic_index': [70, 81, 90, 86, 84, 100, 94],
    })

    signals = detect_peaks_realtime(
        dff,
        entry_threshold=80,
        fall_back_pct=0.05,
        signal_col='panic_index',
    )

    assert signals.index.tolist() == [4, 6]
    assert signals['panic_index'].tolist() == [84, 94]
