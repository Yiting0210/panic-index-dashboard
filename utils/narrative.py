from .metrics import max_drawdown


def _format_pct(value):
    return f"{value:.1f}%"


def _classify_regime(latest_panic, buy_threshold, sell_threshold):
    if latest_panic > buy_threshold:
        return (
            "Panic regime",
            "the framework would treat this as a threshold-based "
            "accumulation or exposure-increase environment",
        )
    if latest_panic < sell_threshold:
        return (
            "Greed regime",
            "the framework treats this as a risk-warning regime rather than "
            "an equally reliable immediate exit signal",
        )
    return (
        "Neutral regime",
        "no threshold-driven exposure change is indicated",
    )


def _diagnose_performance(return_gap, drawdown_improvement):
    if return_gap >= 0 and drawdown_improvement > 0:
        return (
            "In this sample, the strategy improved both cumulative return "
            "and downside protection."
        )
    if return_gap < 0 and drawdown_improvement > 0:
        return (
            "In this sample, the strategy achieved modest downside protection "
            "at the cost of substantial upside participation, consistent with "
            "the project's market asymmetry thesis."
        )
    if return_gap < 0 and drawdown_improvement <= 0:
        return (
            "In this sample, the strategy underperformed Buy & Hold on both "
            "return and drawdown dimensions."
        )
    return (
        "In this sample, the strategy delivered higher return, but that came "
        "with greater downside risk."
    )


def _diagnostic_hypothesis(return_gap, drawdown_improvement):
    if drawdown_improvement <= 0:
        return (
            "This may reflect de-risking rules that activated too late or "
            "failed to reduce exposure during the main drawdown windows."
        )
    if return_gap < 0:
        return (
            "This may reflect the weakness of treating extreme greed as an "
            "immediate exit signal: prolonged underexposure during sustained "
            "uptrends can offset the benefit of panic-side accumulation."
        )
    return (
        "This should be tested for stability across market phases rather than "
        "assuming the improvement is persistent."
    )


def _format_exposure(value):
    return f"{value * 100:.1f}%"


def _attribution_evidence(return_gap, diagnostics, attribution):
    if not diagnostics or not attribution:
        return None
    if return_gap >= 0:
        return None

    average_exposure = diagnostics.get("average_exposure")
    upside_drag = attribution.get("upside_drag")
    downside_cushion = attribution.get("downside_cushion")
    if average_exposure is None or upside_drag is None or downside_cushion is None:
        return None
    if average_exposure >= 0.95:
        return None

    exposure_text = f"average exposure was {_format_exposure(average_exposure)}"
    if upside_drag > downside_cushion:
        return (
            "The return gap is consistent with lower market participation: "
            f"{exposure_text}, while reduced exposure coincided with "
            f"approximately {upside_drag:.1f} percentage points of uncaptured "
            f"positive market return versus {downside_cushion:.1f} points of "
            "downside cushion on an arithmetic attribution basis. This "
            "suggests the panic-side accumulation rule may be more useful than "
            "treating greed as a symmetric exit trigger in this sample."
        )
    if downside_cushion > upside_drag:
        return (
            "The return gap should be read alongside the defensive trade-off: "
            f"{exposure_text}, and reduced exposure coincided with "
            f"approximately {downside_cushion:.1f} percentage points of "
            f"downside cushion versus {upside_drag:.1f} points of uncaptured "
            "positive market return on an arithmetic attribution basis."
        )
    return None


def _exposure_evidence(return_gap, drawdown_improvement, diagnostics):
    if not diagnostics:
        return None
    if not (return_gap < 0 and drawdown_improvement > 0):
        return None

    average_exposure = diagnostics.get("average_exposure")
    time_below_full = diagnostics.get("time_below_full_exposure")
    if average_exposure is None or time_below_full is None:
        return None
    if average_exposure >= 0.95 or time_below_full < 0.20:
        return None

    return (
        "The return gap is consistent with lower market participation: "
        f"average exposure was {_format_exposure(average_exposure)} and "
        f"{_format_exposure(time_below_full)} of observations were below "
        "full exposure."
    )


def _next_tests(return_gap, drawdown_improvement):
    if return_gap < 0 and drawdown_improvement > 0:
        return (
            "whether the exposure trade-off is concentrated in specific "
            "low-exposure episodes or is sensitive to the greed-side "
            "risk-warning threshold"
        )
    if return_gap < 0 and drawdown_improvement <= 0:
        return (
            "drawdown attribution, market-regime performance, threshold "
            "sensitivity, and risk-adjusted metrics"
        )
    if return_gap >= 0 and drawdown_improvement <= 0:
        return (
            "drawdown attribution, exposure during selloffs, threshold "
            "sensitivity, and Sharpe / Sortino / Calmar ratios"
        )
    return (
        "market-regime performance, threshold sensitivity, and Sharpe / "
        "Sortino / Calmar ratios"
    )


def build_threshold_research_summary(
    dff,
    bt,
    target_col,
    selected_label,
    buy_threshold,
    sell_threshold,
    diagnostics=None,
    attribution=None,
):
    latest = dff.dropna(subset=['panic_index', target_col]).iloc[-1]
    latest_panic = latest['panic_index']
    regime, interpretation = _classify_regime(
        latest_panic,
        buy_threshold,
        sell_threshold,
    )

    strategy_return = bt['cumulative_strategy'].iloc[-1] - 100
    buyhold_return = bt['cumulative_buyhold'].iloc[-1] - 100
    return_gap = strategy_return - buyhold_return
    strategy_drawdown = max_drawdown(bt['cumulative_strategy'])
    buyhold_drawdown = max_drawdown(bt['cumulative_buyhold'])
    drawdown_improvement = strategy_drawdown - buyhold_drawdown
    performance_diagnosis = _diagnose_performance(
        return_gap,
        drawdown_improvement,
    )
    diagnostic_hypothesis = _diagnostic_hypothesis(
        return_gap,
        drawdown_improvement,
    )
    attribution_evidence = _attribution_evidence(
        return_gap,
        diagnostics,
        attribution,
    )
    exposure_evidence = _exposure_evidence(
        return_gap,
        drawdown_improvement,
        diagnostics,
    )
    next_tests = _next_tests(return_gap, drawdown_improvement)
    diagnostic_sentence = (
        attribution_evidence
        or exposure_evidence
        or diagnostic_hypothesis
    )

    return (
        f"{selected_label} is currently in a {regime} with a Panic Index of "
        f"{latest_panic:.1f}, so {interpretation}. Over the selected period, "
        f"the strategy returned {_format_pct(strategy_return)}, compared "
        f"with {_format_pct(buyhold_return)} for Buy & Hold, while maximum "
        f"drawdown moved from {_format_pct(buyhold_drawdown)} to "
        f"{_format_pct(strategy_drawdown)}. {performance_diagnosis} "
        f"{diagnostic_sentence} The next diagnostic step is to test "
        f"{next_tests}."
    )
