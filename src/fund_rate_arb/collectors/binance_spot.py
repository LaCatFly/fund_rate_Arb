"""Binance spot market collector.

Uses ccxt with defaultType: "spot". No futures, no papi namespace.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import ccxt

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.collectors.portfolio_margin import OrderResult
from fund_rate_arb.config import BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)


class BinanceSpotCollector(BaseCollector):
    """Binance spot market via ccxt (defaultType: spot)."""

    @property
    def exchange_name(self) -> str:
        return "binance_spot"

    def __init__(self) -> None:
        api_key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_SECRET", "")
        if not api_key or not secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET must be set in .env")

        self._exchange: ccxt.binance | None = None
        self._api_key = api_key
        self._secret = secret

    @property
    def exchange(self) -> ccxt.binance:
        if self._exchange is None:
            self._exchange = ccxt.binance({
                "apiKey": self._api_key,
                "secret": self._secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "spot",
                },
            })
            proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
            if proxy:
                self._exchange.proxies = {"http": proxy, "https": proxy}
            self._exchange.load_markets()
        return self._exchange

    # -- BaseCollector interface (no-op for spot) --

    async def fetch_funding_rates(self) -> list[FundingRate]:
        return []

    async def fetch_open_interest(self) -> list[OpenInterest]:
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        return []

    # -- Spot balance --

    def fetch_balance(self) -> dict[str, float]:
        balance = self.exchange.fetch_balance()
        return balance.get("total", {})

    # -- Trading --

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        **kwargs: Any,
    ) -> OrderResult:
        order = self.exchange.create_order(
            symbol=symbol, type=order_type, side=side,
            amount=amount, price=price,
        )
        return OrderResult(
            order_id=str(order["id"]), symbol=order["symbol"],
            side=order["side"], type=order["type"],
            amount=order["amount"], price=order.get("price"),
            filled=order.get("filled", 0), status=order["status"],
            average=order.get("average"), raw=order,
        )

    def fetch_positions(self) -> list[dict[str, Any]]:
        return []
