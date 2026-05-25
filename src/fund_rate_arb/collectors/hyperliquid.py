"""Hyperliquid perpetuals data collector."""

from __future__ import annotations

import httpx
from datetime import datetime

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import HYPERLIQUID_API
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class HyperliquidCollector(BaseCollector):
    """Collects funding, OI, and spread data from Hyperliquid."""

    @property
    def exchange_name(self) -> str:
        return "hyperliquid"

    async def _post_info(self, body: dict) -> dict | list:
        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            resp = await client.post("/info", json=body, timeout=30.0)
            resp.raise_for_status()
            return resp.json()

    async def fetch_funding_rates(self) -> list[FundingRate]:
        """Fetch funding rates for all active perps on Hyperliquid.

        Hyperliquid API:
        - POST /info with {"type": "meta"} gives coin metadata (names, indices)
        - POST /info with {"type": "allMids"} gives current mark prices
        - Funding is embedded in meta response per asset
        """
        meta = await self._post_info({"type": "meta"})
        mids = await self._post_info({"type": "allMids"})

        # mids is a dict of coin_name -> mid price
        mid_prices = {}
        if isinstance(mids, dict):
            for coin, price in mids.items():
                try:
                    mid_prices[coin.lower()] = float(price)
                except (ValueError, TypeError):
                    pass

        now = datetime.utcnow()
        results = []

        # Meta response has "universe" array with perp assets
        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        for asset in universe:
            name = asset.get("name", "").upper()
            if not name:
                continue

            # Hyperliquid funding rate is stored in "funding" field
            # It's the 8-hour funding rate as a decimal
            raw_funding = asset.get("funding", "0")
            try:
                funding_rate = float(raw_funding)
            except (ValueError, TypeError):
                continue

            # Convert to symbol format: BTC -> BTCUSDT for cross-exchange matching
            symbol = f"{name}USDT"
            mark_price = mid_prices.get(name.lower())

            results.append(FundingRate(
                symbol=symbol,
                exchange="hyperliquid",
                timestamp=now,
                funding_rate=funding_rate,
                predicted_rate=None,
                mark_price=mark_price,
                index_price=mark_price,
            ))

        return results

    async def fetch_open_interest(self) -> list[OpenInterest]:
        """Fetch OI for all perps.

        Hyperliquid: POST /info with {"type": "meta"} + stats
        OI is in the asset meta as "openInterest" (in coin units)
        """
        meta = await self._post_info({"type": "meta"})
        now = datetime.utcnow()
        results = []

        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        for asset in universe:
            name = asset.get("name", "").upper()
            if not name:
                continue

            # Hyperliquid OI may be in different fields
            oi = asset.get("openInterest")
            if oi is None:
                continue
            try:
                oi_val = float(oi)
            except (ValueError, TypeError):
                continue
            if oi_val <= 0:
                continue

            symbol = f"{name}USDT"
            results.append(OpenInterest(
                symbol=symbol,
                exchange="hyperliquid",
                timestamp=now,
                open_interest=oi_val,
            ))

        return results

    async def fetch_spreads(self) -> list[SpreadData]:
        """Fetch bid/ask spreads.

        Hyperliquid: POST /info with {"type": "l2Book", "coin": "BTC"} for each asset.
        This is expensive to do for all assets sequentially, so we use allMids
        as a proxy and estimate spread from the mid.
        """
        meta = await self._post_info({"type": "meta"})
        now = datetime.utcnow()
        results = []

        universe = meta.get("universe", []) if isinstance(meta, dict) else []
        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            for asset in universe:
                name = asset.get("name", "")
                if not name:
                    continue
                try:
                    l2 = await client.post(
                        "/info",
                        json={"type": "l2Book", "coin": name},
                        timeout=10.0,
                    )
                    l2.raise_for_status()
                    book = l2.json()
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
                    symbol = f"{name.upper()}USDT"
                    results.append(SpreadData(
                        symbol=symbol,
                        exchange="hyperliquid",
                        timestamp=now,
                        bid=best_bid,
                        ask=best_ask,
                        spread_bps=round(spread_bps, 2),
                    ))
                except Exception:
                    continue

        return results
