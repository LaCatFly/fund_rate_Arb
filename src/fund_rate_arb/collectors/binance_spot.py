"""Binance spot collector via Convert API for Ondo tokenized stocks.

Equity spot tokens (TSLAon, NVDAon, etc.) are Binance Alpha assets traded
exclusively through the Convert API (/sapi/v1/convert/*), not the standard
spot order book.

API flow:
    1. GET  /sapi/v1/convert/exchangeInfo  — verify pair + size limits
    2. POST /sapi/v1/convert/getQuote      — signed, returns live ratio (15s valid)
    3. POST /sapi/v1/convert/acceptQuote   — signed, executes the conversion
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from typing import Any

import httpx

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.collectors.portfolio_margin import OrderResult
from fund_rate_arb.config import BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)

SAPI_BASE = "https://api.binance.com"


class BinanceSpotCollector(BaseCollector):
    """Binance spot via Convert API for Ondo tokenized stocks."""

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

    def _proxy(self) -> dict[str, str] | None:
        proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
        return {"http://": proxy, "https://": proxy} if proxy else None

    def _sign(self, params: dict[str, str]) -> str:
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        return hmac.new(
            self._secret.encode(), qs.encode(), hashlib.sha256
        ).hexdigest()

    def _http(self) -> httpx.Client:
        return httpx.Client(proxies=self._proxy(), timeout=15)

    # -- Convert API methods --

    def fetch_convert_exchange_info(
        self, from_asset: str, to_asset: str
    ) -> dict[str, Any]:
        """Verify pair availability and get size limits."""
        with self._http() as client:
            resp = client.get(
                f"{SAPI_BASE}/sapi/v1/convert/exchangeInfo",
                params={"fromAsset": from_asset, "toAsset": to_asset},
            )
        resp.raise_for_status()
        return resp.json()

    def fetch_convert_price(
        self,
        from_asset: str,
        to_asset: str,
        amount: float = 100.0,
    ) -> float:
        """Get live price via Convert API. Returns price of to_asset in from_asset."""
        timestamp = str(int(time.time() * 1000))
        params: dict[str, str] = {
            "fromAsset": from_asset,
            "toAsset": to_asset,
            "fromAmount": str(amount),
            "timestamp": timestamp,
        }
        params["signature"] = self._sign(params)
        headers = {"X-MBX-APIKEY": self._api_key}
        with self._http() as client:
            resp = client.post(
                f"{SAPI_BASE}/sapi/v1/convert/getQuote",
                params=params,
                headers=headers,
            )
        data = resp.json()
        if "inverseRatio" not in data:
            logger.warning("Convert quote failed: %s", data)
            return 0.0
        return float(data["inverseRatio"])

    def accept_quote(self, quote_id: str) -> dict[str, Any]:
        """Accept a Convert quote to execute the trade."""
        timestamp = str(int(time.time() * 1000))
        params: dict[str, str] = {
            "quoteId": quote_id,
            "timestamp": timestamp,
        }
        params["signature"] = self._sign(params)
        headers = {"X-MBX-APIKEY": self._api_key}
        with self._http() as client:
            resp = client.post(
                f"{SAPI_BASE}/sapi/v1/convert/acceptQuote",
                params=params,
                headers=headers,
            )
        return resp.json()

    # -- BaseCollector interface (no-op for spot) --

    async def fetch_funding_rates(self) -> list[FundingRate]:
        return []

    async def fetch_open_interest(self) -> list[OpenInterest]:
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        return []

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
        """Execute spot trade via Convert API getQuote + acceptQuote.

        symbol: Ondo token name (e.g., "TSLAon")
        side: "buy" (USDT→token) or "sell" (token→USDT)
        amount: USDT amount for buy, token amount for sell
        """
        if side == "buy":
            from_asset, to_asset = "USDT", symbol
            from_amount = str(amount)
        else:
            from_asset, to_asset = symbol, "USDT"
            from_amount = str(amount)

        timestamp = str(int(time.time() * 1000))
        params: dict[str, str] = {
            "fromAsset": from_asset,
            "toAsset": to_asset,
            "fromAmount": from_amount,
            "timestamp": timestamp,
        }
        params["signature"] = self._sign(params)
        headers = {"X-MBX-APIKEY": self._api_key}

        with self._http() as client:
            quote_resp = client.post(
                f"{SAPI_BASE}/sapi/v1/convert/getQuote",
                params=params,
                headers=headers,
            )
        quote_data = quote_resp.json()
        if "quoteId" not in quote_data:
            logger.error("Convert getQuote failed: %s", quote_data)
            return OrderResult(
                order_id="", symbol=symbol, side=side, type="convert",
                amount=amount, price=None, filled=0, status="failed",
                average=None, raw=quote_data,
            )

        accept_data = self.accept_quote(quote_data["quoteId"])
        order_status = "closed" if accept_data.get("orderStatus") == "SUCCESS" else "failed"
        avg_price = float(accept_data.get("ratio", 0)) if side == "buy" else float(accept_data.get("inverseRatio", 0))

        return OrderResult(
            order_id=str(accept_data.get("orderId", "")),
            symbol=symbol,
            side=side,
            type="convert",
            amount=amount,
            price=avg_price,
            filled=float(accept_data.get("toAmount", 0)),
            status=order_status,
            average=avg_price,
            raw=accept_data,
        )

    def fetch_positions(self) -> list[dict[str, Any]]:
        return []

    def fetch_balance(self) -> dict[str, float]:
        """Fetch spot wallet balances via signed API."""
        timestamp = str(int(time.time() * 1000))
        params: dict[str, str] = {"timestamp": timestamp}
        params["signature"] = self._sign(params)
        headers = {"X-MBX-APIKEY": self._api_key}
        with self._http() as client:
            resp = client.get(
                f"{SAPI_BASE}/sapi/v1/capital/config/getall",
                params=params,
                headers=headers,
            )
        data = resp.json()
        if isinstance(data, list):
            return {
                item["coin"]: float(item["free"])
                for item in data
                if float(item.get("free", 0)) > 0
            }
        return {}
