"""Binance portfolio margin collector and trading engine via CCXT.

Uses papi.binance.com endpoints (PM v2). Implements BaseCollector for
funding/OI/spread collection, plus trading methods for order execution.

API mapping (Binance PM doc -> CCXT method):
    GET  /papi/v1/account               -> exchange.papi_get_account()
    GET  /papi/v1/balance                -> exchange.papi_get_balance()
    GET  /papi/v1/um/positionRisk         -> exchange.fetch_positions(params={'papi': True})
    GET  /papi/v1/repay-futures-switch    -> exchange.papi_get_repay_futures_switch()
    POST /papi/v1/um/order                -> exchange.create_order(params={'papi': True})
    POST /papi/v1/margin/order            -> exchange.create_order(params={'papi': True})
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import ccxt

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import BINANCE_PROXY, BINANCE_FUTURES_BASE
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)


@dataclass
class PMAccountInfo:
    """Portfolio margin account snapshot from GET /papi/v1/account."""
    account_type: str
    total_account_balance: float
    total_maintenance_margin: float
    total_initial_margin: float
    total_margin_balance: float
    available_balance: float
    max_withdraw_amount: float
    positions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OrderResult:
    """Result of a placed order."""
    order_id: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float | None
    filled: float
    status: str
    average: float | None
    position_side: str | None = None
    raw: dict[str, Any] | None = None


class PortfolioMarginCollector(BaseCollector):
    """PM data collection + trading via CCXT papi namespace."""

    @property
    def exchange_name(self) -> str:
        return "binance_pm"

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
        """Lazy CCXT exchange instance."""
        if self._exchange is None:
            self._exchange = ccxt.binance({
                "apiKey": self._api_key,
                "secret": self._secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "future",
                    "papi": True,
                },
            })
            proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
            if proxy:
                self._exchange.proxies = {
                    "http": proxy,
                    "https": proxy,
                }
            self._exchange.load_markets()
            # Set default leverage to 1x for all USDT perpetual markets
            for symbol in self._exchange.markets:
                m = self._exchange.markets[symbol]
                if m.get("swap") and "USDT" in symbol:
                    try:
                        self._exchange.set_leverage(1, symbol)
                    except Exception as e:
                        logger.warning("set_leverage 1x failed %s: %s", symbol, e)
        return self._exchange

    # -- BaseCollector interface (async wrappers) --

    async def fetch_funding_rates(self) -> list[FundingRate]:
        now = datetime.utcnow()
        symbols = [s for s in self.exchange.markets if self.exchange.markets[s].get("swap") and "USDT" in s]
        results = []
        for sym in symbols:
            try:
                ticker = self.exchange.fetch_ticker(sym)
                mark = ticker.get("last") or ticker.get("close")
                if mark:
                    results.append(FundingRate(
                        symbol=sym,
                        exchange=self.exchange_name,
                        timestamp=now,
                        funding_rate=0.0,  # PM API doesn't expose premiumIndex directly
                        mark_price=mark,
                    ))
            except Exception as e:
                logger.warning("PM ticker failed %s: %s", sym, e)
        return results

    async def fetch_open_interest(self) -> list[OpenInterest]:
        # PM doesn't expose OI directly — return empty
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        now = datetime.utcnow()
        results = []
        for sym in self.exchange.markets:
            m = self.exchange.markets[sym]
            if not m.get("swap") or "USDT" not in sym:
                continue
            try:
                ob = self.exchange.fetch_order_book(sym, limit=1)
                if ob.get("bids") and ob.get("asks"):
                    bid = ob["bids"][0][0]
                    ask = ob["asks"][0][0]
                    if bid > 0 and ask > 0:
                        spread_bps = ((ask - bid) / bid) * 10000
                        results.append(SpreadData(
                            symbol=sym,
                            exchange=self.exchange_name,
                            timestamp=now,
                            bid=bid,
                            ask=ask,
                            spread_bps=round(spread_bps, 2),
                        ))
            except Exception as e:
                logger.warning("PM spread failed %s: %s", sym, e)
        return results

    # -- Account info --

    def fetch_account_info(self) -> PMAccountInfo:
        result = self.exchange.papi_get_account()
        return PMAccountInfo(
            account_type=result.get("accountType", "PORTFOLIO_MARGIN"),
            total_account_balance=float(result.get("accountEquity", 0)),
            total_maintenance_margin=float(result.get("accountMaintMargin", 0)),
            total_initial_margin=float(result.get("accountInitialMargin", 0)),
            total_margin_balance=float(result.get("actualEquity", 0)),
            available_balance=float(result.get("totalAvailableBalance", 0)),
            max_withdraw_amount=float(result.get("virtualMaxWithdrawAmount", 0)),
        )

    def fetch_balance(self) -> list[dict[str, Any]]:
        return self.exchange.papi_get_balance()

    # -- Positions --

    def fetch_positions(self) -> list[dict[str, Any]]:
        positions = self.exchange.fetch_positions(params={"papi": True})
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

    def fetch_um_account(self) -> dict[str, Any]:
        return self.exchange.papi_get_um_account()

    def fetch_cm_account(self) -> dict[str, Any]:
        return self.exchange.papi_get_cm_account()

    def fetch_auto_repay_status(self) -> dict[str, Any]:
        return self.exchange.papi_get_repay_futures_switch()

    def fetch_leverage_brackets(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"papi": True}
        if symbol:
            params["symbol"] = symbol
        return self.exchange.fetch_leverage_tiers(symbol, params)

    def fetch_income_history(self, income_type: str | None = None, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"papi": True}
        if income_type:
            params["incomeType"] = income_type
        if symbol:
            params["symbol"] = symbol
        return self.exchange.papi_get_um_income(params)

    def fetch_interest_history(self, asset: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if asset:
            params["asset"] = asset
        return self.exchange.papi_get_portfolio_interest_history(params)

    # -- Trading methods --

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
        """Place order via PM (papi.binance.com). Hedge mode requires position_side."""
        p: dict[str, Any] = {"papi": True}
        if position_side:
            p["positionSide"] = position_side
        if params:
            p.update(params)

        order = self.exchange.create_order(
            symbol=symbol,
            type=order_type,
            side=side,
            amount=amount,
            price=price,
            params=p,
        )
        return OrderResult(
            order_id=str(order["id"]),
            symbol=order["symbol"],
            side=order["side"],
            type=order["type"],
            amount=order["amount"],
            price=order.get("price"),
            filled=order.get("filled", 0),
            status=order["status"],
            average=order.get("average"),
            position_side=position_side,
            raw=order,
        )

    def close_position(
        self,
        symbol: str,
        amount: float,
        position_side: str,
    ) -> OrderResult:
        """Close position with market order. No reduceRequired flag."""
        close_side = "sell" if position_side == "LONG" else "buy"
        return self.place_order(
            symbol=symbol,
            side=close_side,
            amount=amount,
            order_type="market",
            position_side=position_side,
        )
