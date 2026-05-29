"""Cached Alpha token prices with fallback to DB.

Provides a simple lookup for Ondo tokenized stock spot prices via the
Binance Alpha API. Results are cached for 60 seconds and persisted
to the alpha_price_cache table for resilience.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import httpx

from fund_rate_arb.config import BINANCE_PROXY, UNDERLYINGS

logger = logging.getLogger(__name__)

_ALPHA_API_BASE = "https://www.binance.com"
_CACHE: dict[str, float] = {}
_CACHE_TS: float = 0
_CACHE_TTL = 60  # seconds


def get_alpha_prices(db_path: str | None = None, force_refresh: bool = False) -> dict[str, float]:
    """Return {symbol: price} for equity tokens in our whitelist.

    Uses in-memory cache (60s TTL), falls back to DB on API failure.
    """
    global _CACHE, _CACHE_TS

    if not force_refresh and _CACHE and (time.time() - _CACHE_TS) < _CACHE_TTL:
        return dict(_CACHE)

    prices = _fetch_alpha_prices()

    if prices:
        _CACHE = prices
        _CACHE_TS = time.time()
        if db_path:
            _persist_prices(db_path, prices)
        return dict(prices)

    # API failed — try DB fallback
    if db_path:
        cached = _load_cached_prices(db_path)
        if cached:
            logger.info("Alpha API unavailable, using cached prices from DB")
            return cached

    logger.warning("Alpha API and DB cache both empty — no spot prices")
    return {}


def _fetch_alpha_prices() -> dict[str, float]:
    """Hit the Alpha API and return filtered prices."""
    try:
        proxy_url = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
        resp = httpx.get(
            f"{_ALPHA_API_BASE}/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list",
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
        logger.warning("Alpha API error: code=%s", data.get("code"))
        return {}

    spot_symbols = {
        u.binance_spot for u in UNDERLYINGS if u.binance_spot is not None
    }

    results = {}
    for token in data.get("data", []):
        sym = token.get("symbol", "")
        if sym in spot_symbols:
            try:
                results[sym] = float(token.get("price", "0"))
            except (ValueError, TypeError):
                pass

    return results


def _persist_prices(db_path: str, prices: dict[str, float]) -> None:
    """INSERT OR REPLACE into alpha_price_cache."""
    import sqlite3

    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alpha_price_cache (
                symbol TEXT PRIMARY KEY,
                price REAL NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        for sym, price in prices.items():
            conn.execute(
                "INSERT OR REPLACE INTO alpha_price_cache (symbol, price, ts) VALUES (?, ?, ?)",
                (sym, price, ts),
            )


def _load_cached_prices(db_path: str) -> dict[str, float]:
    """Load last-known prices from alpha_price_cache."""
    import sqlite3

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT symbol, price FROM alpha_price_cache").fetchall()
            return {r["symbol"]: r["price"] for r in rows}
    except Exception:
        return {}
