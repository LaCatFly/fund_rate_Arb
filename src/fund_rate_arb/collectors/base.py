"""Base collector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class BaseCollector(ABC):
    """Abstract base for exchange data collectors."""

    @property
    @abstractmethod
    def exchange_name(self) -> str: ...

    @abstractmethod
    async def fetch_funding_rates(self) -> list[FundingRate]:
        """Fetch current funding rates for all symbols."""

    @abstractmethod
    async def fetch_open_interest(self) -> list[OpenInterest]:
        """Fetch current open interest for all symbols."""

    @abstractmethod
    async def fetch_spreads(self) -> list[SpreadData]:
        """Fetch current bid/ask spreads."""

    async def fetch_all(self) -> tuple[list[FundingRate], list[OpenInterest], list[SpreadData]]:
        """Fetch all data concurrently."""
        import asyncio
        funding, oi, spreads = await asyncio.gather(
            self.fetch_funding_rates(),
            self.fetch_open_interest(),
            self.fetch_spreads(),
        )
        return funding, oi, spreads
