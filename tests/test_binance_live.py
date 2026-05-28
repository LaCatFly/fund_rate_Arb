"""Live integration tests against Binance portfolio margin account.

Uses papi.binance.com for PM endpoints. Spot orders use regular spot API
(GLM in spot wallet). Futures use PM with hedge mode (LONG/SHORT positionSide).

Minimum notional on Binance: $5.00 per order. Tests use $5.50 to be safe.
"""

from __future__ import annotations

import os
import time

import ccxt
import pytest

SYMBOL_SPOT = "GLM/USDT"
SYMBOL_FUTURES = "DOT/USDT:USDT"
SPOT_SELL_AMOUNT = 40  # ~40 GLM × $0.14 ≈ $5.60
FUTURES_NOTIONAL = 5.50  # safely above $5 minimum


@pytest.fixture
def spot_exchange():
    """Regular spot API — for GLM trades."""
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_SECRET", "")
    if not api_key or not secret:
        pytest.skip("BINANCE_API_KEY and BINANCE_SECRET not set")

    ex = ccxt.binance(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    proxy = os.environ.get("BINANCE_PROXY", "")
    if proxy:
        ex.proxies = {"http": proxy, "https": proxy}
    ex.load_markets()
    return ex


@pytest.fixture
def pm_exchange():
    """PM API — for futures + account info. Hedge mode requires positionSide."""
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_SECRET", "")
    if not api_key or not secret:
        pytest.skip("BINANCE_API_KEY and BINANCE_SECRET not set")

    ex = ccxt.binance(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future", "papi": True},
        }
    )
    proxy = os.environ.get("BINANCE_PROXY", "")
    if proxy:
        ex.proxies = {"http": proxy, "https": proxy}
    ex.load_markets()
    return ex


def _futures_amount(exchange, notional):
    """Calculate contract amount for target notional, safely above $5."""
    ticker = exchange.fetch_ticker(SYMBOL_FUTURES)
    price = ticker["last"]
    amount = round(notional / price, 2)
    actual = amount * price
    print(f"  Price: {price}, amount: {amount}, notional: {actual:.2f}")
    return amount


class TestPortfolioMarginAccount:
    """Verify PM account connectivity."""

    def test_pm_account_info(self, pm_exchange):
        """GET /papi/v1/account."""
        account = pm_exchange.papi_get_account()
        print(f"\n  Account equity:  {account['accountEquity']} USDT")
        print(f"  Available:       {account['totalAvailableBalance']} USDT")
        print(f"  Status:          {account['accountStatus']}")
        assert float(account["accountEquity"]) > 0
        assert account["accountStatus"] == "NORMAL"

    def test_pm_balance(self, pm_exchange):
        """GET /papi/v1/balance — returns list of assets in PM wallet."""
        balance = pm_exchange.papi_get_balance()
        usdt = [a for a in balance if a["asset"] == "USDT"]
        assert len(usdt) == 1
        print(f"\n  PM USDT walletBalance: {usdt[0]['totalWalletBalance']}")

    def test_spot_has_glm(self, spot_exchange):
        """Verify GLM in spot wallet."""
        bal = spot_exchange.fetch_balance()
        glm_free = bal.get("GLM", {}).get("free", 0)
        print(f"\n  GLM free: {glm_free}")
        assert glm_free >= SPOT_SELL_AMOUNT


class TestSpotOrders:
    """Sell/buy GLM via regular spot API."""

    def test_sell_glm_for_usdt(self, spot_exchange):
        """Market sell 40 GLM → USDT."""
        bal_before = spot_exchange.fetch_balance()
        glm_before = bal_before.get("GLM", {}).get("free", 0)
        usdt_before = bal_before.get("USDT", {}).get("free", 0)
        print(f"\n  Before: GLM={glm_before}, USDT={usdt_before}")

        order = spot_exchange.create_order(
            symbol=SYMBOL_SPOT,
            type="market",
            side="sell",
            amount=SPOT_SELL_AMOUNT,
        )
        print(
            f"  Sell: id={order['id']}, filled={order['filled']}, avg={order.get('average')}"
        )

        time.sleep(2)
        bal_after = spot_exchange.fetch_balance()
        usdt_after = bal_after.get("USDT", {}).get("free", 0)
        print(f"  After USDT: {usdt_after}")
        assert usdt_after > (usdt_before or 0)

    def test_buy_glm_with_usdt(self, spot_exchange):
        """Buy GLM back with available USDT."""
        bal = spot_exchange.fetch_balance()
        usdt_free = bal.get("USDT", {}).get("free", 0)
        if usdt_free < 5:
            pytest.skip(f"Need $5 USDT, have {usdt_free}")

        ticker = spot_exchange.fetch_ticker(SYMBOL_SPOT)
        glm_amount = round(5.50 / ticker["last"], 1)
        print(f"\n  Buying {glm_amount} GLM at ~{ticker['last']}")

        order = spot_exchange.create_order(
            symbol=SYMBOL_SPOT,
            type="market",
            side="buy",
            amount=glm_amount,
        )
        print(
            f"  Buy: id={order['id']}, filled={order['filled']}, avg={order.get('average')}"
        )

        time.sleep(2)
        bal_after = spot_exchange.fetch_balance()
        print(f"  GLM free: {bal_after['GLM']['free']}")


class TestFuturesOrders:
    """Open/close DOT/USDT:USDT via PM (hedge mode — requires positionSide)."""

    def test_open_and_close_futures_long(self, pm_exchange):
        """Long DOT → close with opposite sell (no reduceOnly)."""
        amount = _futures_amount(pm_exchange, FUTURES_NOTIONAL)
        print(f"\n  Opening LONG {amount} DOT")

        order = pm_exchange.create_order(
            symbol=SYMBOL_FUTURES,
            type="market",
            side="buy",
            amount=amount,
            params={"papi": True, "positionSide": "LONG"},
        )
        print(
            f"  Open LONG: id={order['id']}, status={order['status']}, filled={order.get('filled')}"
        )
        assert order["status"] in ("closed", "filled", "open")

        # Use actual filled amount
        filled = order.get("filled") or amount
        print(f"  Closing LONG with sell of {filled} DOT")

        close = pm_exchange.create_order(
            symbol=SYMBOL_FUTURES,
            type="market",
            side="sell",
            amount=filled,
            params={"papi": True, "positionSide": "LONG"},
        )
        print(f"  Close: id={close['id']}, status={close['status']}")

    def test_open_and_close_futures_short(self, pm_exchange):
        """Short DOT → close with opposite buy (no reduceOnly)."""
        amount = _futures_amount(pm_exchange, FUTURES_NOTIONAL)
        print(f"\n  Opening SHORT {amount} DOT")

        order = pm_exchange.create_order(
            symbol=SYMBOL_FUTURES,
            type="market",
            side="sell",
            amount=amount,
            params={"papi": True, "positionSide": "SHORT"},
        )
        print(
            f"  Open SHORT: id={order['id']}, status={order['status']}, filled={order.get('filled')}"
        )
        assert order["status"] in ("closed", "filled", "open")

        filled = order.get("filled") or amount
        print(f"  Closing SHORT with buy of {filled} DOT")

        close = pm_exchange.create_order(
            symbol=SYMBOL_FUTURES,
            type="market",
            side="buy",
            amount=filled,
            params={"papi": True, "positionSide": "SHORT"},
        )
        print(f"  Close: id={close['id']}, status={close['status']}")
