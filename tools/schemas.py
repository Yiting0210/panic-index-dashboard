"""Structured result models returned by every research tool.

Each result carries ``as_of`` (the caller's information cutoff) and
``data_through`` (the latest observation actually used), so clients and
tests can verify that nothing after the cutoff was read.
"""
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Market = Literal["QQQ", "SMH", "SPX", "DJI"]
Regime = Literal["panic", "greed", "neutral"]


class PointInTime(BaseModel):
    as_of: date = Field(description="Information cutoff requested by the caller.")
    data_through: date = Field(description="Latest observation date used in the result.")


class RegimeResult(PointInTime):
    market: Market
    observation_date: date
    panic_index: float = Field(description="Composite Panic Index (0-100) recomputed on data up to as_of.")
    vix: float
    fear_greed_index: float
    fear_greed_rating: Optional[str]
    regime: Regime
    buy_threshold: float
    sell_threshold: float
    panic_index_percentile: float = Field(
        description="Percentile of the latest value within the history available at as_of (0-100)."
    )
    price: float
    interpretation: str


class BacktestResult(PointInTime):
    market: Market
    start_date: date
    observations: int
    buy_threshold: float
    sell_threshold: float
    strategy_return_pct: float
    buyhold_return_pct: float
    strategy_max_drawdown_pct: float
    buyhold_max_drawdown_pct: float
    strategy_sharpe: Optional[float]
    buyhold_sharpe: Optional[float]
    strategy_calmar: Optional[float]
    buyhold_calmar: Optional[float]
    average_exposure_pct: float
    time_below_full_exposure_pct: float
    upside_drag_pp: float = Field(description="Arithmetic uncaptured upside from reduced exposure, in percentage points.")
    downside_cushion_pp: float = Field(description="Arithmetic avoided downside from reduced exposure, in percentage points.")
    final_position_pct: float = Field(description=(
        "Exposure the rule holds after the latest observation (0-100). Path "
        "dependent: it changes with start_date and initial_position_pct."
    ))
    latest_panic_index: float
    latest_regime: Regime
    initial_position_pct: float
    add_pct: float
    reduce_pct: float
    max_position_pct: float
    min_position_pct: float
    position_rule: str = Field(description="The add/reduce rule this backtest applied.")
    verdict: str


class HorizonStats(BaseModel):
    horizon_days: int
    count: int
    win_rate_pct: Optional[float]
    mean_return_pct: Optional[float]
    median_return_pct: Optional[float]
    min_return_pct: Optional[float]
    max_return_pct: Optional[float]
    last_signal_date: Optional[date] = Field(
        description="Latest signal date whose full forward window ends on or before as_of."
    )


class ForwardReturnsResult(PointInTime):
    market: Market
    signal: Literal["panic", "greed"]
    threshold: float
    start_date: date
    signal_days: int = Field(description="Days in the signal regime between start_date and as_of.")
    horizons: list[HorizonStats]
    note: str


class PeakEvent(BaseModel):
    date: date
    panic_index: float
    price: float


class PeaksResult(PointInTime):
    market: Market
    method: Literal["realtime", "scipy"]
    hindsight: bool = Field(description="True when labels need observations after the peak date.")
    start_date: date
    total_events: int
    events: list[PeakEvent] = Field(description="Most recent events first, capped at max_events.")
    note: str


class FedDocChunk(BaseModel):
    doc_id: str
    chunk_id: str
    title: str
    doc_type: Literal["statement", "minutes"]
    meeting_date: date
    published_at: date
    url: str
    score: float
    text: str


class FedDocSearchResult(BaseModel):
    query: str
    as_of: date
    total_eligible_chunks: int = Field(description="Chunks published on or before as_of that were searchable.")
    results: list[FedDocChunk]
