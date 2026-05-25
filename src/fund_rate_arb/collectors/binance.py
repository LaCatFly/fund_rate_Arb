"""Binance USDT perpetuals data collector."""

from __future__ import annotations

import httpx
from datetime import datetime

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import BINANCE_FUTURES_BASE, WHITELIST_BINANCE
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class BinanceCollector(BaseCollector):
    """Collects funding, OI, and spread data from Binance USDT-M futures."""

    @property
    def exchange_name(self) -> str:
        return "binance"

    async def fetch_funding_rates(self) -> list[FundingRate]:
        """GET /fapi/v1/premiumIndex — returns all symbols with funding info."""
        async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE) as client:
            resp = await client.get("/fapi/v1/premiumIndex", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

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
        """GET /fapi/v1/openInterest — aggregate OI per symbol."""
        async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE) as client:
            info_resp = await client.get("/fapi/v1/exchangeInfo", timeout=30.0)
            info_resp.raise_for_status()
            symbols = [
                s["symbol"] for s in info_resp.json()["symbols"]
                if s["symbol"] in WHITELIST_BINANCE and s["contractType"] == "PERPETUAL"
            ]

        now = datetime.utcnow()
        results = []
        async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE) as client:
            # Fetch OI for each symbol concurrently
            tasks = []
            for sym in symbols:
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
        except Exception:
            return None

    async def fetch_spreads(self) -> list[SpreadData]:
        """GET /fapi/v1/ticker/bookTicker — best bid/ask for all symbols."""
        async with httpx.AsyncClient(base_url=BINANCE_FUTURES_BASE) as client:
            resp = await client.get("/fapi/v1/ticker/bookTicker", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()

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
    import asyncio
    semaphore = asyncio.Semaphore(limit)

    async def bounded(task):
        async with semaphore:
            try:
                return await task
            except Exception:
                return None

    return await asyncio.gather(*[bounded(t) for t in tasks])
