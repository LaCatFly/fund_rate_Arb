"""Binance USDT perpetuals data collector."""

from __future__ import annotations

import asyncio
import logging

import httpx
from datetime import datetime

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import BINANCE_FUTURES_BASE, WHITELIST_BINANCE, BINANCE_PROXY
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData

logger = logging.getLogger(__name__)


class BinanceCollector(BaseCollector):
    """Collects funding, OI, and spread data from Binance USDT-M futures."""

    @property
    def exchange_name(self) -> str:
        return "binance"

    async def fetch_funding_rates(self) -> list[FundingRate]:
        """GET /fapi/v1/premiumIndex — returns all symbols with funding info."""
        try:
            async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE, proxy=BINANCE_PROXY) as client:
                resp = await client.get("/fapi/v1/premiumIndex", timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error("Binance fetch_funding_rates failed: %s", e)
            return []

        now = datetime.utcnow()
        results = []
        for item in data:
            symbol = item.get("symbol", "")
            if symbol not in WHITELIST_BINANCE:
                continue

            results.append(FundingRate(
                symbol=symbol,
                exchange="binance",
                timestamp=now,
                funding_rate=float(item["lastFundingRate"]),
                predicted_rate=float(item["lastFundingRate"]),
                mark_price=float(item["markPrice"]),
                index_price=float(item["indexPrice"]),
            ))
        return results

    async def fetch_open_interest(self) -> list[OpenInterest]:
        """GET /fapi/v1/openInterest — query OI for each whitelisted symbol."""
        now = datetime.utcnow()
        results = []
        async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE) as client:
            tasks = []
            for sym in WHITELIST_BINANCE:
                tasks.append(self._fetch_single_oi(client, sym, now))
            for result in await asyncio_gather_safe(tasks, limit=20):
                if result is not None:
                    results.append(result)
        return results

    async def _fetch_single_oi(
        self, client: httpx.AsyncClient, symbol: str, now: datetime
    ) -> OpenInterest | None:
        try:
            resp = await client.get(
                "/fapi/v1/openInterest",
                params={"symbol": symbol},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            oi = float(data["openInterest"])
            if oi <= 0:
                return None
            return OpenInterest(
                symbol=symbol,
                exchange="binance",
                timestamp=now,
                open_interest=oi,
            )
        except httpx.HTTPError as e:
            logger.warning("Binance OI fetch failed for %s: %s", symbol, e)
            return None
        except (KeyError, ValueError) as e:
            logger.warning("Binance OI parse failed for %s: %s", symbol, e)
            return None

    async def fetch_spreads(self) -> list[SpreadData]:
        """GET /fapi/v1/ticker/bookTicker — best bid/ask for all symbols."""
        try:
            async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE, proxy=BINANCE_PROXY) as client:
                resp = await client.get("/fapi/v1/ticker/bookTicker", timeout=30.0)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.error("Binance fetch_spreads failed: %s", e)
            return []

        now = datetime.utcnow()
        results = []
        for item in data:
            symbol = item.get("symbol", "")
            if symbol not in WHITELIST_BINANCE:
                continue
            bid = float(item["bidPrice"])
            ask = float(item["askPrice"])
            if bid <= 0 or ask <= 0:
                continue
            spread_bps = ((ask - bid) / bid) * 10000  # basis points
            results.append(SpreadData(
                symbol=symbol,
                exchange="binance",
                timestamp=now,
                bid=bid,
                ask=ask,
                spread_bps=round(spread_bps, 2),
            ))
        return results


async def asyncio_gather_safe(tasks: list, limit: int = 20) -> list:
    """Run tasks with concurrency limit, suppressing individual failures."""
    semaphore = asyncio.Semaphore(limit)

    async def bounded(task):
        async with semaphore:
            try:
                return await task
            except Exception:
                return None

    return await asyncio.gather(*[bounded(t) for t in tasks])
