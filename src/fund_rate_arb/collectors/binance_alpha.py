"""Binance Alpha API collector for Ondo tokenized stocks.

Fetches spot prices for equity tokens (TSLAon, NVDAon, etc.) from the
Binance Alpha market data API. Unauthenticated public endpoint.

API: GET /bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list
"""

from __future__ import annotations

import logging
import os

import httpx

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)

ALPHA_API_BASE = "https://www.binance.com"


class BinanceAlphaCollector(BaseCollector):
    """Collects spot prices for Ondo tokenized equities via Binance Alpha API."""

    @property
    def exchange_name(self) -> str:
        return "binance_alpha"

    def fetch_all_token_prices(self) -> dict[str, float]:
        """Fetch all Alpha token prices, return filtered dict {symbol: price}.

        Returns prices for equity tokens in our whitelist only.
        """
        try:
            proxy_url = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
            resp = httpx.get(
                f"{ALPHA_API_BASE}/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list",
                headers={"Content-Type": "application/json"},
                proxy=proxy_url,
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("Alpha API fetch failed: %s", e)
            return {}

        if not data.get("success"):
            logger.warning("Alpha API returned error: %s", data.get("code"))
            return {}

        # Build set of whitelisted equity spot symbols
        from fund_rate_arb.config import UNDERLYINGS
        spot_symbols = {
            u.binance_spot for u in UNDERLYINGS if u.binance_spot is not None
        }

        results = {}
        for token in data.get("data", []):
            sym = token.get("symbol", "")
            if sym in spot_symbols:
                price_str = token.get("price", "0")
                try:
                    results[sym] = float(price_str)
                except (ValueError, TypeError):
                    logger.warning("Invalid price for %s: %s", sym, price_str)

        return results

    async def fetch_funding_rates(self) -> list[FundingRate]:
        return []

    async def fetch_open_interest(self) -> list[OpenInterest]:
        return []

    async def fetch_spreads(self) -> list[SpreadData]:
        return []
