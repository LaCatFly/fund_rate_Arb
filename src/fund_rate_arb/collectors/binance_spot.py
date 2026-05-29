"""Binance spot collector placeholder — Ondo token spot prices come via BinanceAlphaCollector.

Convert API (/sapi/v1/convert/*) confirmed offline for all Ondo tokens (code 345122).
Spot execution is unavailable; paper mode is the only working path.
"""

from __future__ import annotations

import os

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class BinanceSpotCollector(BaseCollector):
    """Placeholder — paper mode only for Ondo token spot execution."""

    @property
    def exchange_name(self) -> str:
        return "binance_spot"

    def __init__(self) -> None:
        api_key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_SECRET", "")
        if not api_key or not secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET must be set in .env")
        self._api_key = api_key
        self._secret = secret

    async def fetch_funding_rates(self) -> list[FundingRate]:
        return []

    async def fetch_open_interest(self) -> list[OpenInterest]:
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        return []

    def fetch_positions(self) -> list[dict]:
        return []

    def fetch_balance(self) -> dict[str, float]:
        return {}
