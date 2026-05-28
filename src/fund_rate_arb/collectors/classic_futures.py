"""Classic USDT-M futures collector — standard Binance API key (not PM).

Uses fapi.binance.com endpoints via CCXT swap type. No papi namespace.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import ccxt

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.collectors.portfolio_margin import OrderResult
from fund_rate_arb.config import BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)


@dataclass
class ClassicAccountInfo:
    """USDT-M futures account snapshot."""
    account_type: str = "CLASSIC"
    total_account_balance: float = 0.0
    total_maintenance_margin: float = 0.0
    total_initial_margin: float = 0.0
    total_margin_balance: float = 0.0
    available_balance: float = 0.0
    max_withdraw_amount: float = 0.0
    account_status: str = "NORMAL"
    positions: list[dict[str, Any]] = field(default_factory=list)


class ClassicFuturesCollector(BaseCollector):
    """USDT-M futures via standard ccxt (no papi)."""

    @property
    def exchange_name(self) -> str:
        return "binance"

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
                    "defaultType": "swap",
                },
            })
            proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
            if proxy:
                self._exchange.proxies = {"http": proxy, "https": proxy}
            self._exchange.load_markets()
        return self._exchange

    # -- BaseCollector interface --

    async def fetch_funding_rates(self) -> list[FundingRate]:
        return []

    async def fetch_open_interest(self) -> list[OpenInterest]:
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        return []

    # -- Account --

    def fetch_account_info(self) -> ClassicAccountInfo:
        info = self.exchange.fapiprivatev2_get_account()
        return ClassicAccountInfo(
            total_account_balance=float(info.get("totalWalletBalance", 0)),
            available_balance=float(info.get("availableBalance", 0)),
            total_margin_balance=float(info.get("totalMarginBalance", 0)),
            total_initial_margin=float(info.get("totalInitialMargin", 0)),
            total_maintenance_margin=float(info.get("totalMaintMargin", 0)),
            max_withdraw_amount=float(info.get("availableBalance", 0)),
        )

    def fetch_balance(self) -> dict[str, float]:
        balance = self.exchange.fetch_balance()
        return balance.get("total", {})

    # -- Positions --

    def fetch_positions(self) -> list[dict[str, Any]]:
        positions = self.exchange.fetch_positions()
        return [
            {
                "symbol": p["symbol"],
                "side": p["side"],
                "contracts": p["contracts"],
                "entry_price": p["entryPrice"],
                "unrealized_pnl": p.get("unrealizedPnl", 0.0),
                "maintenance_margin": p.get("maintenanceMargin", 0.0),
                "initial_margin": p.get("initialMargin", 0.0),
                "leverage": p.get("leverage", 1),
            }
            for p in positions
            if p["contracts"] and p["contracts"] > 0
        ]

    # -- Trading --

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: float | None = None,
        position_side: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> OrderResult:
        p: dict[str, Any] = {}
        if position_side:
            p["positionSide"] = position_side
        if params:
            p.update(params)

        order = self.exchange.create_order(
            symbol=symbol, type=order_type, side=side,
            amount=amount, price=price, params=p,
        )
        return OrderResult(
            order_id=str(order["id"]), symbol=order["symbol"],
            side=order["side"], type=order["type"],
            amount=order["amount"], price=order.get("price"),
            filled=order.get("filled", 0), status=order["status"],
            average=order.get("average"), position_side=position_side, raw=order,
        )

    def close_position(
        self,
        symbol: str,
        amount: float,
        position_side: str,
    ) -> OrderResult:
        close_side = "sell" if position_side == "LONG" else "buy"
        return self.place_order(
            symbol=symbol, side=close_side, amount=amount,
            order_type="market", position_side=position_side,
        )
