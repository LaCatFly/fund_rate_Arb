"""Hyperliquid equity perps data collector."""

from __future__ import annotations

import asyncio
import httpx
from datetime import datetime, timezone

from fund_rate_arb.collectors.base import BaseCollector
from fund_rate_arb.config import HYPERLIQUID_API, WHITELIST_HYPERLIQUID, HIP3_PREFIX, FUNDING_LOOKBACK_HOURS
from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class HyperliquidCollector(BaseCollector):
    """Collects funding, OI, and spread data from Hyperliquid equity perps.

    Uses native Hyperliquid POST /info API:
    - fundingHistory: hourly funding rate per coin (requires startTime)
    - metaAndAssetCtxs: current mark prices, OI, and per-asset context
    - l2Book: orderbook depth for spread calculation

    Note: metaAndAssetCtxs does not include HIP-3 (xyz:) assets.
    OI is only available for core perps.
    """

    @property
    def exchange_name(self) -> str:
        return "hyperliquid"

    async def _post_info(self, body: dict, timeout: float = 30.0) -> dict | list:
        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            resp = await client.post("/info", json=body, timeout=timeout)
            resp.raise_for_status()
            return resp.json()

    async def fetch_funding_rates(self) -> list[FundingRate]:
        """Fetch latest funding rates for whitelisted equity perps.

        Uses a 24h lookback window to ensure fresh rates.
        Hyperliquid funding rates are hourly.
        """
        now = datetime.now(timezone.utc)
        start_ms = int((now.timestamp() - FUNDING_LOOKBACK_HOURS * 3600) * 1000)
        results = []

        for ticker in WHITELIST_HYPERLIQUID:
            coin = f"{HIP3_PREFIX}{ticker}"
            try:
                data = await self._post_info({
                    "type": "fundingHistory",
                    "coin": coin,
                    "startTime": start_ms,
                })
                if not data:
                    continue
                latest = data[-1]
                rate = float(latest["fundingRate"])
                premium = float(latest["premium"])
                results.append(FundingRate(
                    symbol=f"{ticker}USDT",
                    exchange="hyperliquid",
                    timestamp=now,
                    funding_rate=rate,
                    predicted_rate=premium,
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

        Note: HIP-3 (xyz:) assets are not included in metaAndAssetCtxs.
        """
        data = await self._post_info({"type": "metaAndAssetCtxs"})
        if not isinstance(data, list) or len(data) < 2:
            return []

        universe = data[0].get("universe", []) if isinstance(data[0], dict) else []
        ctxs = data[1] if isinstance(data[1], list) else []

        now = datetime.now(timezone.utc)
        results = []
        for i, asset in enumerate(universe):
            name = asset.get("name", "")
            # Skip HIP-3 assets — they don't have OI in metaAndAssetCtxs
            if name.startswith(HIP3_PREFIX):
                continue
            ticker = name[len(HIP3_PREFIX):] if name.startswith(HIP3_PREFIX) else name
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

    async def _fetch_spread_for_ticker(self, ticker: str, client: httpx.AsyncClient) -> SpreadData | None:
        """Fetch bid/ask spread for a single ticker via l2Book."""
        coin = f"{HIP3_PREFIX}{ticker}"
        try:
            resp = await client.post(
                "/info",
                json={"type": "l2Book", "coin": coin},
                timeout=10.0,
            )
            resp.raise_for_status()
            book = resp.json()

            # Handle both response formats:
            # Old: [bids_list, asks_list]
            # New: {"levels": [[bids_list], [asks_list]], ...}
            if isinstance(book, dict):
                levels = book.get("levels")
                if not isinstance(levels, list) or len(levels) < 2:
                    return None
                bids_raw, asks_raw = levels[0], levels[1]
            elif isinstance(book, list) and len(book) >= 2:
                bids_raw, asks_raw = book[0], book[1]
            else:
                return None

            bids = [b for b in bids_raw if float(b.get("px", 0)) > 0]
            asks = [a for a in asks_raw if float(a.get("px", 0)) > 0]
            if not bids or not asks:
                return None
            best_bid = float(bids[0]["px"])
            best_ask = float(asks[0]["px"])
            if best_bid <= 0 or best_ask <= 0:
                return None
            mid_price = (best_bid + best_ask) / 2
            spread_bps = ((best_ask - best_bid) / best_bid) * 10000
            return SpreadData(
                symbol=f"{ticker}USDT",
                exchange="hyperliquid",
                timestamp=datetime.now(timezone.utc),
                bid=best_bid,
                ask=best_ask,
                spread_bps=round(spread_bps, 2),
                mark_price=round(mid_price, 2),
            )
        except Exception:
            return None

    async def fetch_spreads(self) -> list[SpreadData]:
        """Fetch bid/ask spreads via l2Book for each whitelisted equity perp.

        Uses concurrent requests via a shared httpx.AsyncClient session.
        """
        async with httpx.AsyncClient(base_url=HYPERLIQUID_API) as client:
            tasks = [
                self._fetch_spread_for_ticker(ticker, client)
                for ticker in WHITELIST_HYPERLIQUID
            ]
            spread_results = await asyncio.gather(*tasks)

        return [r for r in spread_results if r is not None]
