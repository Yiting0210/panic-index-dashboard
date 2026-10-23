"""Research tools shared by the MCP server and the Streamlit assistant.

Every tool takes an ``as_of`` date and only reads observations dated on or
before it. Results are pydantic models, so MCP clients receive a JSON schema
and structured content instead of free text.
"""
import math
from datetime import date
from typing import Annotated, Literal, Optional

from pydantic import Field

from utils.analysis import (build_backtest_verdict,
                            build_exposure_tradeoff_attribution,
                            build_strategy_diagnostics)
from utils.backtest import run_backtest as _run_backtest
from utils.metrics import build_performance_metrics
from utils.narrative import build_threshold_research_summary
from utils.peak_detection import detect_peaks_realtime, detect_peaks_scipy
from utils.strategy import DEFAULT_STRATEGY, StrategyParams

from .market import MARKETS, ToolInputError, get_store, to_date
from .schemas import (BacktestResult, FedDocSearchResult, ForwardReturnsResult,
                      HorizonStats, Market, PeakEvent, PeaksResult,
                      RegimeResult)

AsOf = Annotated[date, Field(
    description=(
        "Information cutoff (YYYY-MM-DD). Only market data and documents "
        "dated on or before this day are used."
    )
)]
MarketArg = Annotated[Market, Field(description="Market to analyze.")]
StartDate = Annotated[Optional[date], Field(
    description="First date to include. Defaults to the start of the data."
)]
BuyThreshold = Annotated[float, Field(
    ge=0, le=100, description="Panic Index level above which the market is in the panic regime."
)]
SellThreshold = Annotated[float, Field(
    ge=0, le=100, description="Panic Index level below which the market is in the greed regime."
)]

DEFAULT_BUY_THRESHOLD = DEFAULT_STRATEGY.buy_threshold
DEFAULT_SELL_THRESHOLD = DEFAULT_STRATEGY.sell_threshold
PositionPct = Annotated[float, Field(ge=0, le=100)]


def _num(value, digits=4):
    """Round a numeric value, mapping NaN/inf to None for JSON output."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, digits)


def _classify(panic_index, buy_threshold, sell_threshold):
    if panic_index > buy_threshold:
        return "panic", (
            "Panic regime: the framework treats this as a threshold-based "
            "accumulation / exposure-increase environment."
        )
    if panic_index < sell_threshold:
        return "greed", (
            "Greed regime: treated as a risk-warning signal, not an equally "
            "reliable immediate exit signal."
        )
    return "neutral", "Neutral regime: no threshold-driven exposure change is indicated."


def _frame(market, as_of, start_date=None):
    as_of = to_date(as_of)
    df = get_store().frame_as_of(as_of, start_date=start_date)
    target_col = MARKETS[market][0]
    df = df.dropna(subset=['panic_index', target_col]).reset_index(drop=True)
    if df.empty:
        raise ToolInputError(f"No {market} observations between {start_date} and {as_of}.")
    return df, target_col, as_of


def get_regime(
    market: MarketArg,
    as_of: AsOf,
    buy_threshold: BuyThreshold = DEFAULT_BUY_THRESHOLD,
    sell_threshold: SellThreshold = DEFAULT_SELL_THRESHOLD,
) -> RegimeResult:
    """Classify the latest Panic Index regime for a market as of a date.

    Returns the Composite Panic Index (VIX 50% + inverted CNN Fear & Greed
    50%), its percentile within the history available at as_of, and whether it
    sits in the panic, greed, or neutral regime.
    """
    df, target_col, as_of = _frame(market, as_of)
    latest = df.iloc[-1]
    regime, interpretation = _classify(latest['panic_index'], buy_threshold, sell_threshold)
    percentile = (df['panic_index'] <= latest['panic_index']).mean() * 100
    observation_date = latest['date_dt'].date()
    return RegimeResult(
        market=market,
        as_of=as_of,
        data_through=observation_date,
        observation_date=observation_date,
        panic_index=_num(latest['panic_index'], 2),
        vix=_num(latest['vix'], 2),
        fear_greed_index=_num(latest['fear_greed_index'], 2),
        fear_greed_rating=latest.get('fear_greed_rating'),
        regime=regime,
        buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
        panic_index_percentile=_num(percentile, 1),
        price=_num(latest[target_col], 2),
        interpretation=interpretation,
    )


def run_backtest(
    market: MarketArg,
    as_of: AsOf,
    start_date: StartDate = None,
    buy_threshold: BuyThreshold = DEFAULT_BUY_THRESHOLD,
    sell_threshold: SellThreshold = DEFAULT_SELL_THRESHOLD,
    initial_position_pct: PositionPct = DEFAULT_STRATEGY.initial_position_pct,
    add_pct: Annotated[float, Field(ge=0, le=100, description="Exposure added per panic-regime day.")] = DEFAULT_STRATEGY.add_pct,
    reduce_pct: Annotated[float, Field(ge=0, le=100, description="Exposure removed per greed-regime day.")] = DEFAULT_STRATEGY.reduce_pct,
    max_position_pct: PositionPct = DEFAULT_STRATEGY.max_position_pct,
    min_position_pct: PositionPct = DEFAULT_STRATEGY.min_position_pct,
) -> BacktestResult:
    """Backtest the Panic Index threshold strategy against Buy & Hold.

    Exposure rises by add_pct on each panic-regime day and falls by
    reduce_pct on each greed-regime day, within min/max_position_pct, applied
    from the next observation. Reports return, max drawdown, Sharpe, Calmar,
    average exposure, the upside-drag / downside-cushion attribution of
    reduced exposure, and final_position_pct: the exposure the rule holds
    now. Use final_position_pct and position_rule for any position-size
    question. The backtest ends at as_of.
    """
    if min_position_pct > max_position_pct:
        raise ToolInputError("min_position_pct cannot exceed max_position_pct.")
    df, target_col, as_of = _frame(market, as_of, start_date)
    if len(df) < 3:
        raise ToolInputError("Need at least 3 observations to run a backtest.")
    params = StrategyParams(
        buy_threshold=buy_threshold, sell_threshold=sell_threshold,
        initial_position_pct=initial_position_pct, add_pct=add_pct,
        reduce_pct=reduce_pct, max_position_pct=max_position_pct,
        min_position_pct=min_position_pct,
    )
    bt = _run_backtest(
        df, target_col,
        pos_initial=initial_position_pct, add_amount=add_pct,
        reduce_amount=reduce_pct, pos_max=max_position_pct,
        pos_min=min_position_pct, buy_threshold=buy_threshold,
        sell_threshold=sell_threshold,
    )
    latest_panic = bt['panic_index'].iloc[-1]
    metrics = build_performance_metrics(bt)
    diagnostics = build_strategy_diagnostics(bt)
    attribution = build_exposure_tradeoff_attribution(bt)
    return BacktestResult(
        market=market,
        as_of=as_of,
        data_through=bt['date_dt'].iloc[-1].date(),
        start_date=bt['date_dt'].iloc[0].date(),
        observations=len(bt),
        strategy_return_pct=_num(metrics['strategy_return'], 2),
        buyhold_return_pct=_num(metrics['buyhold_return'], 2),
        strategy_max_drawdown_pct=_num(metrics['strategy_max_drawdown'], 2),
        buyhold_max_drawdown_pct=_num(metrics['buyhold_max_drawdown'], 2),
        strategy_sharpe=_num(metrics['strategy_sharpe'], 3),
        buyhold_sharpe=_num(metrics['buyhold_sharpe'], 3),
        strategy_calmar=_num(metrics['strategy_calmar'], 3),
        buyhold_calmar=_num(metrics['buyhold_calmar'], 3),
        average_exposure_pct=_num(diagnostics['average_exposure'] * 100, 1),
        time_below_full_exposure_pct=_num(diagnostics['time_below_full_exposure'] * 100, 1),
        upside_drag_pp=_num(attribution['upside_drag'], 2),
        downside_cushion_pp=_num(attribution['downside_cushion'], 2),
        final_position_pct=_num(bt['position'].iloc[-1] * 100, 1),
        latest_panic_index=_num(latest_panic, 2),
        latest_regime=_classify(latest_panic, buy_threshold, sell_threshold)[0],
        **params.as_dict(),
        position_rule=params.describe(),
        verdict=build_backtest_verdict(bt),
    )


def get_forward_returns(
    market: MarketArg,
    as_of: AsOf,
    signal: Annotated[Literal["panic", "greed"], Field(
        description="Regime whose subsequent returns are measured."
    )] = "panic",
    threshold: Annotated[Optional[float], Field(
        ge=0, le=100,
        description="Regime threshold. Defaults to 64 for panic and 15 for greed.",
    )] = None,
    horizons: Annotated[list[int], Field(
        description="Forward horizons in trading days, e.g. [5, 21, 63]."
    )] = [5, 21, 63],
    start_date: StartDate = None,
) -> ForwardReturnsResult:
    """Summarize forward returns after panic- or greed-regime days.

    Only signal days whose full forward window ends on or before as_of are
    counted, so a forward return is never computed from prices after as_of.
    """
    if threshold is None:
        threshold = DEFAULT_BUY_THRESHOLD if signal == "panic" else DEFAULT_SELL_THRESHOLD
    df, target_col, as_of = _frame(market, as_of, start_date)
    mask = df['panic_index'] > threshold if signal == "panic" else df['panic_index'] < threshold

    stats = []
    for horizon in sorted(set(horizons)):
        if horizon <= 0:
            raise ToolInputError("Horizons must be positive trading-day counts.")
        fwd = (df[target_col].shift(-horizon) / df[target_col] - 1) * 100
        returns = fwd[mask].dropna()
        last_signal = df.loc[returns.index, 'date_dt'].max() if not returns.empty else None
        stats.append(HorizonStats(
            horizon_days=horizon,
            count=len(returns),
            win_rate_pct=_num((returns > 0).mean() * 100, 1) if len(returns) else None,
            mean_return_pct=_num(returns.mean(), 2),
            median_return_pct=_num(returns.median(), 2),
            min_return_pct=_num(returns.min(), 2),
            max_return_pct=_num(returns.max(), 2),
            last_signal_date=last_signal.date() if last_signal is not None else None,
        ))

    return ForwardReturnsResult(
        market=market,
        as_of=as_of,
        data_through=df['date_dt'].iloc[-1].date(),
        signal=signal,
        threshold=threshold,
        start_date=df['date_dt'].iloc[0].date(),
        signal_days=int(mask.sum()),
        horizons=stats,
        note=(
            "Regime days are overlapping exposure days, not independent "
            "bottom forecasts. Historical evidence, not guaranteed returns."
        ),
    )


def detect_peaks(
    market: MarketArg,
    as_of: AsOf,
    method: Annotated[Literal["realtime", "scipy"], Field(
        description=(
            "'realtime' is the causal panic-reversal detector (no look-ahead). "
            "'scipy' is scipy.signal.find_peaks, a hindsight label."
        )
    )] = "realtime",
    start_date: StartDate = None,
    entry_threshold: Annotated[float, Field(ge=0, le=100, description="realtime: Panic Index level that opens the watch zone.")] = 80,
    fall_back_pct: Annotated[float, Field(gt=0, lt=100, description="realtime: % retreat from the local max that triggers a signal.")] = 5,
    prominence: Annotated[float, Field(gt=0, description="scipy: minimum peak prominence.")] = 10,
    distance: Annotated[int, Field(ge=1, description="scipy: minimum days between peaks.")] = 10,
    max_events: Annotated[int, Field(ge=1, le=100)] = 20,
) -> PeaksResult:
    """Find Panic Index peaks / panic-reversal signals up to as_of.

    The realtime method only uses past and current values and is suitable for
    backtests. The scipy method labels a peak using values on both sides of it,
    so it is flagged as hindsight and should be used only for validation.
    """
    df, target_col, as_of = _frame(market, as_of, start_date)
    if method == "realtime":
        peaks = detect_peaks_realtime(df, entry_threshold=entry_threshold,
                                      fall_back_pct=fall_back_pct / 100)
        note = "Causal signals: each date is when the reversal was confirmed, using no later data."
    else:
        peaks = detect_peaks_scipy(df, prominence=prominence, distance=distance)
        note = (
            "Hindsight labels: each peak is confirmed by values after it, all on "
            "or before as_of. Do not treat these dates as tradable signals."
        )
    events = [
        PeakEvent(
            date=row['date_dt'].date(),
            panic_index=_num(row['panic_index'], 2),
            price=_num(row[target_col], 2),
        )
        for _, row in peaks.sort_values('date_dt', ascending=False).head(max_events).iterrows()
    ]
    return PeaksResult(
        market=market,
        as_of=as_of,
        data_through=df['date_dt'].iloc[-1].date(),
        method=method,
        hindsight=method == "scipy",
        start_date=df['date_dt'].iloc[0].date(),
        total_events=len(peaks),
        events=events,
        note=note,
    )


def search_fed_docs(
    query: Annotated[str, Field(min_length=3, description="Natural-language search query.")],
    as_of: AsOf,
    k: Annotated[int, Field(ge=1, le=10, description="Number of passages to return.")] = 5,
    doc_type: Annotated[Optional[Literal["statement", "minutes", "press_conference"]], Field(
        description="Restrict to policy statements, meeting minutes, or Chair press "
                    "conference transcripts."
    )] = None,
) -> FedDocSearchResult:
    """Search FOMC statements, minutes, and Chair press conferences published on or before as_of.

    Statements and press conferences come out on the decision day; minutes
    are dated by their public release (about three weeks after the meeting),
    so a query as of a meeting date will not see that meeting's minutes.
    Press conferences give the Chair's own framing and carry the Chair's name
    in ``speaker``; minutes show how views spread across the Committee. Each
    passage includes its source URL for citation.
    """
    from rag import get_index

    as_of = to_date(as_of)
    hits, eligible = get_index().search(query, as_of=as_of, k=k, doc_type=doc_type)
    return FedDocSearchResult(query=query, as_of=as_of,
                              total_eligible_chunks=eligible, results=hits)


def rule_based_summary(market, as_of, start_date=None,
                       buy_threshold=DEFAULT_BUY_THRESHOLD,
                       sell_threshold=DEFAULT_SELL_THRESHOLD):
    """Deterministic fallback summary used when the LLM path is unavailable."""
    df, target_col, as_of = _frame(market, as_of, start_date)
    bt = _run_backtest(df, target_col,
                       buy_threshold=buy_threshold, sell_threshold=sell_threshold)
    return build_threshold_research_summary(
        df, bt, target_col, MARKETS[market][1], buy_threshold, sell_threshold,
        diagnostics=build_strategy_diagnostics(bt),
        attribution=build_exposure_tradeoff_attribution(bt),
    )


RESEARCH_TOOLS = [get_regime, run_backtest, get_forward_returns, detect_peaks, search_fed_docs]

__all__ = [fn.__name__ for fn in RESEARCH_TOOLS] + ["RESEARCH_TOOLS", "rule_based_summary"]
