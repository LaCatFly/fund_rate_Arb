"""Tests for PortfolioMarginCollector."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from fund_rate_arb.collectors.portfolio_margin import (
    PortfolioMarginCollector,
    PMAccountInfo,
    OrderResult,
)


@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {
        "BINANCE_API_KEY": "test_api_key",
        "BINANCE_SECRET": "test_secret",
        "BINANCE_PROXY": "",
    }):
        yield


@pytest.fixture
def collector(mock_env):
    with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
        mock_exchange = MagicMock()
        mock_binance.return_value = mock_exchange
        c = PortfolioMarginCollector()
        c._exchange = mock_exchange
        return c


class TestPMAccountInfo:
    def test_default_positions_empty(self):
        info = PMAccountInfo(
            account_type="PORTFOLIO_MARGIN",
            total_account_balance=10000.0,
            total_maintenance_margin=500.0,
            total_initial_margin=1000.0,
            total_margin_balance=9500.0,
            available_balance=9000.0,
            max_withdraw_amount=8500.0,
        )
        assert info.positions == []

    def test_with_positions(self):
        pos = [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.1}]
        info = PMAccountInfo(
            account_type="PORTFOLIO_MARGIN",
            total_account_balance=10000.0,
            total_maintenance_margin=500.0,
            total_initial_margin=1000.0,
            total_margin_balance=9500.0,
            available_balance=9000.0,
            max_withdraw_amount=8500.0,
            positions=pos,
        )
        assert len(info.positions) == 1


class TestOrderResult:
    def test_basic(self):
        r = OrderResult(
            order_id="123",
            symbol="DOT/USDT:USDT",
            side="buy",
            type="market",
            amount=4.2,
            price=None,
            filled=4.2,
            status="closed",
            average=1.285,
            position_side="LONG",
        )
        assert r.order_id == "123"
        assert r.position_side == "LONG"


class TestPortfolioMarginCollectorInit:
    def test_missing_api_key_raises(self):
        with patch.dict(os.environ, {"BINANCE_API_KEY": "", "BINANCE_SECRET": "secret"}):
            with pytest.raises(ValueError, match="BINANCE_API_KEY and BINANCE_SECRET"):
                PortfolioMarginCollector()

    def test_missing_secret_raises(self):
        with patch.dict(os.environ, {"BINANCE_API_KEY": "key", "BINANCE_SECRET": ""}):
            with pytest.raises(ValueError, match="BINANCE_API_KEY and BINANCE_SECRET"):
                PortfolioMarginCollector()

    def test_exchange_name(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance"):
            c = PortfolioMarginCollector()
            assert c.exchange_name == "binance_pm"

    def test_init_sets_exchange_config(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
            mock_instance = MagicMock()
            mock_binance.return_value = mock_instance
            c = PortfolioMarginCollector()
            # Access property to trigger lazy init
            _ = c.exchange
            mock_binance.assert_called_once()
            call_kwargs = mock_binance.call_args[0][0]
            assert call_kwargs["apiKey"] == "test_api_key"
            assert call_kwargs["secret"] == "test_secret"
            assert call_kwargs["enableRateLimit"] is True
            assert call_kwargs["options"]["papi"] is True

    def test_lazy_exchange(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
            c = PortfolioMarginCollector()
            assert c._exchange is None
            _ = c.exchange
            assert c._exchange is not None
            mock_binance.assert_called_once()

    def test_set_leverage_on_all_usdt_swaps(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
            mock_instance = MagicMock()
            mock_instance.markets = {
                "BTC/USDT:USDT": {"swap": True},
                "ETH/USDT:USDT": {"swap": True},
                "SOL/USDT:USDT": {"swap": True},
                "BTC/USD:BTC": {"swap": True},  # non-USDT, skipped
                "DOTUSDT": {"spot": True},  # not swap, skipped
            }
            mock_binance.return_value = mock_instance
            c = PortfolioMarginCollector()
            _ = c.exchange

            calls = [
                call[0] for call in mock_instance.set_leverage.call_args_list
            ]
            assert len(calls) == 3
            assert (1, "BTC/USDT:USDT") in calls
            assert (1, "ETH/USDT:USDT") in calls
            assert (1, "SOL/USDT:USDT") in calls


class TestFetchAccountInfo:
    def test_returns_account_info(self, collector):
        collector.exchange.papi_get_account.return_value = {
            "accountType": "PM_2",
            "accountEquity": "10000.50",
            "actualEquity": "10001.00",
            "accountMaintMargin": "500.25",
            "accountInitialMargin": "1000.00",
            "totalAvailableBalance": "9000.00",
            "virtualMaxWithdrawAmount": "8500.00",
        }
        result = collector.fetch_account_info()

        assert isinstance(result, PMAccountInfo)
        assert result.total_account_balance == 10000.50
        assert result.total_maintenance_margin == 500.25
        assert result.total_margin_balance == 10001.00
        assert result.available_balance == 9000.00

    def test_handles_missing_fields(self, collector):
        collector.exchange.papi_get_account.return_value = {}
        result = collector.fetch_account_info()

        assert result.total_account_balance == 0.0
        assert result.available_balance == 0.0


class TestFetchPositions:
    def test_returns_open_positions(self, collector):
        collector.exchange.fetch_positions.return_value = [
            {
                "symbol": "BTC/USDT:USDT",
                "side": "long",
                "contracts": 0.5,
                "entryPrice": 50000.0,
                "unrealizedPnl": 2500.0,
                "maintenanceMargin": 1000.0,
                "initialMargin": 2000.0,
                "leverage": 10,
            },
            {
                "symbol": "ETH/USDT:USDT",
                "side": "short",
                "contracts": 0,
                "entryPrice": 3000.0,
                "unrealizedPnl": 0,
                "maintenanceMargin": 0,
                "initialMargin": 0,
                "leverage": 1,
            },
        ]
        result = collector.fetch_positions()

        assert len(result) == 1
        assert result[0]["symbol"] == "BTC/USDT:USDT"
        assert result[0]["unrealized_pnl"] == 2500.0

    def test_empty_positions(self, collector):
        collector.exchange.fetch_positions.return_value = []
        result = collector.fetch_positions()
        assert result == []


class TestFetchAutoRepayStatus:
    def test_returns_repay_status(self, collector):
        collector.exchange.papi_get_repay_futures_switch.return_value = {
            "autoRepay": True,
        }
        result = collector.fetch_auto_repay_status()
        assert result["autoRepay"] is True


class TestPlaceOrder:
    def test_place_market_order(self, collector):
        collector.exchange.create_order.return_value = {
            "id": "123",
            "symbol": "DOT/USDT:USDT",
            "side": "buy",
            "type": "market",
            "amount": 4.2,
            "price": None,
            "filled": 4.2,
            "status": "closed",
            "average": 1.285,
        }
        result = collector.place_order(
            symbol="DOT/USDT:USDT",
            side="buy",
            amount=4.2,
            position_side="LONG",
        )
        assert result.order_id == "123"
        assert result.filled == 4.2
        assert result.position_side == "LONG"

        # Verify papi=True was passed
        call_params = collector.exchange.create_order.call_args[1]["params"]
        assert call_params["papi"] is True
        assert call_params["positionSide"] == "LONG"

    def test_place_limit_order(self, collector):
        collector.exchange.create_order.return_value = {
            "id": "456",
            "symbol": "DOT/USDT:USDT",
            "side": "buy",
            "type": "limit",
            "amount": 5.0,
            "price": 1.25,
            "filled": 0,
            "status": "open",
            "average": None,
        }
        result = collector.place_order(
            symbol="DOT/USDT:USDT",
            side="buy",
            amount=5.0,
            order_type="limit",
            price=1.25,
            position_side="LONG",
        )
        assert result.type == "limit"
        assert result.price == 1.25
        assert result.status == "open"


class TestClosePosition:
    def test_close_long(self, collector):
        collector.exchange.create_order.return_value = {
            "id": "789",
            "symbol": "DOT/USDT:USDT",
            "side": "sell",
            "type": "market",
            "amount": 4.2,
            "price": None,
            "filled": 4.2,
            "status": "closed",
            "average": 1.286,
        }
        result = collector.close_position(
            symbol="DOT/USDT:USDT",
            amount=4.2,
            position_side="LONG",
        )
        assert result.side == "sell"
        assert result.position_side == "LONG"
        assert result.type == "market"

        # Verify market order type was explicitly set
        call_type = collector.exchange.create_order.call_args[1]["type"]
        assert call_type == "market"

    def test_close_short(self, collector):
        collector.exchange.create_order.return_value = {
            "id": "790",
            "symbol": "DOT/USDT:USDT",
            "side": "buy",
            "type": "market",
            "amount": 4.2,
            "price": None,
            "filled": 4.2,
            "status": "closed",
            "average": 1.286,
        }
        result = collector.close_position(
            symbol="DOT/USDT:USDT",
            amount=4.2,
            position_side="SHORT",
        )
        assert result.side == "buy"
        assert result.position_side == "SHORT"
