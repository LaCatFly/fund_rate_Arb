"""Collectors package."""
from fund_rate_arb.collectors.base import BaseCollector as BaseCollector
from fund_rate_arb.collectors.binance import BinanceCollector as BinanceCollector
from fund_rate_arb.collectors.binance_spot import BinanceSpotCollector as BinanceSpotCollector
from fund_rate_arb.collectors.classic_futures import ClassicFuturesCollector as ClassicFuturesCollector
from fund_rate_arb.collectors.hyperliquid import HyperliquidCollector as HyperliquidCollector


def detect_key_type() -> str:
    """Try PM papi endpoint; fall back to classic on auth error."""
    import os
    import ccxt
    from fund_rate_arb.config import BINANCE_PROXY

    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_SECRET", "")
    if not api_key or not secret:
        return "none"

    ex = ccxt.binance({
        "apiKey": api_key, "secret": secret,
        "options": {"defaultType": "swap"},
    })
    proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
    if proxy:
        ex.proxies = {"http": proxy, "https": proxy}
    try:
        ex.papi_get_account()
        return "pm"
    except ccxt.AuthenticationError:
        return "classic"
    except Exception:
        return "classic"


def get_trading_collector():
    """Return collector for current key type."""
    kt = detect_key_type()
    if kt == "pm":
        from fund_rate_arb.collectors.portfolio_margin import PortfolioMarginCollector
        return PortfolioMarginCollector()
    from fund_rate_arb.collectors.classic_futures import ClassicFuturesCollector
    return ClassicFuturesCollector()
