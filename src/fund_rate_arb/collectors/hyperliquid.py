"""Hyperliquid equity perps data collector."""

from __future__ import annotations

import httpx
from datetime import datetime

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import HYPERLIQUID_API, WHITELIST_HYPERLIQUID
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


# HIP-3 namespace prefix for equity perps on Hyperliquid
HIP3_PREFIX = "xyz:"


class HyperliquidCollector(BaseCollector):
    """Collects funding, OI, and spread data from Hyperliquid equity perps.

    Uses native Hyperliquid POST /info API:
    - fundingHistory: hourly funding rate per coin (requires startTime)
    - metaAndAssetCtxs: current mark prices, OI, and per-asset context
    - l2Book: orderbook depth for spread calculation
    """

    @property
    def exchange_name(self) -> str:
        return "hyperliquid"

    async def _post_info(self, body: dict) -> dict | list:
        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            resp = await client.post("/info", json=body, timeout=30.0)
            resp.raise_for_status()
            return resp.json()

    async def fetch_funding_rates(self) -> list[FundingRate]:
        """Fetch latest funding rates for whitelisted equity perps.

        For each whitelisted symbol, calls fundingHistory with "xyz:{TICKER}"
        to get the most recent hourly funding entry.
        """
        now = datetime.utcnow()
        results = []

        for ticker in WHITELIST_HYPERLIQUID:
            coin = f"{HIP3_PREFIX}{ticker}"
            try:
                data = await self._post_info({
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": 0,
                })
                if not data:
                    continue
                latest = data[-1]
                results.append(FundingRate(
                    symbol=f"{ticker}USDT",
                    exchange="hyperliquid",
                    timestamp=now,
                    funding_rate=float(latest["fundingRate"]),
                    predicted_rate=None,
                    mark_price=None,
                    index_price=None,
                ))
            except Exception:
                continue

        return results

    async def fetch_open_interest(self) -> list[OpenInterest]:
        """Fetch OI from metaAndAssetCtxs.

        metaAndAssetCtxs returns [meta, ctxs] where ctxs[i] corresponds
        to universe[i]. OI lives in ctxs as "openInterest".
        """
        data = await self._post_info({"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return []

        universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
        ctxs = data[1] if isinstance(data[1], list) else []

        now = datetime.utcnow()
        results = []
        for i, asset in enumerate(universe):
            name = asset.get("name", "")
            if not name.startswith(HIP3_PREFIX):
                continue
            ticker = name[len(HIP3_PREFIX):]
            if ticker not in WHITELIST_HYPERLIQUID:
                continue
            if i >= len(ctxs):
                continue
            ctx = ctxs[i]
            oi_raw = ctx.get("openInterest")
            if oi_raw is None:
                continue
            try:
                oi_val = float(oi_raw)
            except (ValueError, TypeError):
                continue
            if oi_val <= 0:
                continue
            results.append(OpenInterest(
                symbol=f"{ticker}USDT",
                exchange="hyperliquid",
                timestamp=now,
                open_interest=oi_val,
            ))

        return results

    async def fetch_spreads(self) -> list[SpreadData]:
        """Fetch bid/ask spreads via l2Book for each whitelisted equity perp."""
        now = datetime.utcnow()
        results = []

        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            for ticker in WHITELIST_HYPERLIQUID:
                coin = f"{HIP3_PREFIX}{ticker}"
                try:
                    resp = await client.post(
                        "/info",
                        json={"type": "l2Book", "coin": coin},
                        timeout=10.0,
                    )
                    resp.raise_for_status()
                    book = resp.json()
                    if not isinstance(book, list) or len(book) < 2:
                        continue
                    bids = [b for b in book[0] if float(b.get("px", 0)) > 0]
                    asks = [a for a in book[1] if float(a.get("px", 0)) > 0]
                    if not bids or not asks:
                        continue
                    best_bid = float(bids[0]["px"])
                    best_ask = float(asks[0]["px"])
                    if best_bid <= 0 or best_ask <= 0:
                        continue
                    spread_bps = ((best_ask - best_bid) / best_bid) * 10000
                    results.append(SpreadData(
                        symbol=f"{ticker}USDT",
                        exchange="hyperliquid",
                        timestamp=now,
                        bid=best_bid,
                        ask=best_ask,
                        spread_bps=round(spread_bps, 2),
                    ))
                except Exception:
                    continue

        return results
