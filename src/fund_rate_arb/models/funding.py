"""Pydantic data models for funding rate data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, Field


class FundingRate(BaseModel):
    """Single funding rate reading."""
    symbol: str
    exchange: str
    timestamp: datetime
    funding_rate: float = Field(description="Current funding rate per interval")
    predicted_rate: float | None = Field(default=None, description="Next predicted funding rate")
    mark_price: float | None = None
    index_price: float | None = None

    def to_db_row(self) -> tuple:
        return (
            self.symbol,
            self.exchange,
            self.timestamp.isoformat(),
            self.funding_rate,
            self.predicted_rate,
            self.mark_price,
            self.index_price,
        )


class OpenInterest(BaseModel):
    """Open interest snapshot."""
    symbol: str
    exchange: str
    timestamp: datetime
    open_interest: float
    oi_value_usd: float | None = None

    def to_db_row(self) -> tuple:
        return (
            self.symbol,
            self.exchange,
            self.timestamp.isoformat(),
            self.open_interest,
        )


class SpreadData(BaseModel):
    """Orderbook spread snapshot."""
    symbol: str
    exchange: str
    timestamp: datetime
    bid: float
    ask: float
    spread_bps: float  # spread in basis points

    def to_db_row(self) -> tuple:
        return (
            self.symbol,
            self.exchange,
            self.timestamp.isoformat(),
            self.bid,
            self.ask,
            self.spread_bps,
        )


class FundingScore(BaseModel):
    """Computed score for a symbol."""
    symbol: str
    exchange: str
    score: float
    funding_mean: float
    persistence: float  # positive ratio
    volatility: float
    oi_stability: float
    spread_cost_bps: float
    estimated_apy: float
    break_even_days: float
    regime: str = "neutral"  # bull/bear/neutral


@dataclass
class CarryPosition:
    """Runtime position state for a carry trade."""
    execution_id: str           # UUID
    strategy_name: str
    symbol: str
    exchange: str               # "binance_pm" | "paper"
    side: str                   # "SHORT"
    contracts: float
    entry_price: float
    entry_basis: float          # (mark - index) / index at entry
    entry_cost: float           # total fees + slippage
    cumulative_funding: float
    notional_usdt: float
    opened_at: str              # ISO timestamp
    max_break_even_days: int
    status: str                 # "Open" | "Closing" | "Closed"
    close_reason: str | None = None


@dataclass
class ExitSignal:
    position_execution_id: str
    rule_type: str
    severity: str               # "info" | "warning" | "critical"
    message: str


@dataclass
class MarketData:
    """Aggregated market data for a position's monitor cycle."""
    symbol: str
    exchange: str
    current_mark: float
    current_index: float
    current_basis: float
    funding_history_48h: list[float]
    oi_window_8h: list[float]
    distance_to_liq_pct: float | None = None
    predicted_funding: float | None = None


@dataclass
class FundingSummary:
    total_payments: float
    count: int
    average_rate: float
    last_payment_ts: str | None = None
