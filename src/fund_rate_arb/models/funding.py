"""Pydantic data models for funding rate data."""

from __future__ import annotations

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
