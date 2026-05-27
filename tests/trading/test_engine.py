"""Tests for TradingEngine: leverage=1x, close positions at market."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from fund_rate_arb.collectors.portfolio_margin import OrderResult, PortfolioMarginCollector
from fund_rate_arb.trading.engine import Position, TradingEngine, RiskManager


@pytest.fixture
def mock_env():
    with patch.dict(os.environ, {
        "BINANCE_API_KEY": "test_api_key",
        "BINANCE_SECRET": "test_secret",
        "BINANCE_PROXY": "",
    }):
        yield


@pytest.fixture
def mock_collector(mock_env):
    with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
        mock_exchange = MagicMock()
        mock_binance.return_value = mock_exchange
        c = PortfolioMarginCollector()
        c._exchange = mock_exchange
        return c


@pytest.fixture
def engine(mock_collector):
    return TradingEngine(mock_collector, db_path=":memory:")


class TestSetLeverageOnInit:
    def test_set_leverage_called_on_all_usdt_swaps(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
            mock_exchange = MagicMock()
            mock_exchange.markets = {
                "BTC/USDT:USDT": {"swap": True},
                "ETH/USDT:USDT": {"swap": True},
                "SOL/USDT:USDT": {"swap": True},
                "BTC/USD:BTC": {"swap": True},  # not USDT, should be skipped
                "DOTUSDT": {"spot": True},  # not swap, should be skipped
            }
            mock_binance.return_value = mock_exchange

            c = PortfolioMarginCollector()
            _ = c.exchange

            set_leverage_calls = [
                call for call in mock_exchange.set_leverage.call_args_list
            ]
            assert len(set_leverage_calls) == 3
            mock_exchange.set_leverage.assert_any_call(1, "BTC/USDT:USDT")
            mock_exchange.set_leverage.assert_any_call(1, "ETH/USDT:USDT")
            mock_exchange.set_leverage.assert_any_call(1, "SOL/USDT:USDT")

    def test_set_leverage_failure_does_not_break_init(self, mock_env):
        with patch("fund_rate_arb.collectors.portfolio_margin.ccxt.binance") as mock_binance:
            mock_exchange = MagicMock()
            mock_exchange.markets = {"BTC/USDT:USDT": {"swap": True}}
            mock_exchange.set_leverage.side_effect = Exception("API error")
            mock_binance.return_value = mock_exchange

            c = PortfolioMarginCollector()
            _ = c.exchange
            # Should not raise, just logs warning
            assert c._exchange is mock_exchange


class TestFetchLivePositions:
    def test_returns_positions(self, engine, mock_collector):
        mock_collector.fetch_positions = MagicMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
             "entry_price": 50000.0, "unrealized_pnl": 250.0,
             "leverage": 1, "position_side": "LONG"},
            {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 1.0,
             "entry_price": 3000.0, "unrealized_pnl": 0.0,
             "leverage": 1, "position_side": "SHORT"},
        ])
        positions = engine.fetch_live_positions()

        assert len(positions) == 2
        assert positions[0].symbol == "BTC/USDT:USDT"
        assert positions[0].leverage == 1
        assert positions[1].position_side == "SHORT"


class TestCloseAllLongPositions:
    def test_closes_long_positions(self, engine, mock_collector):
        mock_collector.fetch_account_info = MagicMock(return_value=MagicMock(
            available_balance=1000.0, account_status="NORMAL"
        ))
        mock_collector.fetch_positions = MagicMock(return_value=[
            {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.5,
             "entry_price": 50000.0, "unrealized_pnl": 250.0,
             "leverage": 1, "position_side": "LONG"},
            {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 1.0,
             "entry_price": 3000.0, "unrealized_pnl": 0.0,
             "leverage": 1, "position_side": "SHORT"},
        ])
        mock_collector.close_position = MagicMock(return_value=OrderResult(
            order_id="123", symbol="BTC/USDT:USDT", side="sell",
            type="market", amount=0.5, price=None, filled=0.5,
            status="closed", average=50100.0, position_side="LONG",
        ))

        results = engine.close_all_long_positions()

        assert len(results) == 1
        assert results[0].side == "sell"
        assert results[0].type == "market"

    def test_no_longs_returns_empty(self, engine, mock_collector):
        mock_collector.fetch_account_info = MagicMock(return_value=MagicMock(
            available_balance=1000.0, account_status="NORMAL"
        ))
        mock_collector.fetch_positions = MagicMock(return_value=[
            {"symbol": "ETH/USDT:USDT", "side": "short", "contracts": 1.0,
             "entry_price": 3000.0, "unrealized_pnl": 0.0,
             "leverage": 1, "position_side": "SHORT"},
        ])

        results = engine.close_all_long_positions()
        assert results == []

    def test_risk_check_failure_returns_empty(self, engine, mock_collector):
        mock_collector.fetch_account_info = MagicMock(return_value=MagicMock(
            available_balance=1.0, account_status="NORMAL"
        ))

        results = engine.close_all_long_positions()
        assert results == []


class TestClosePosition:
    def test_uses_market_order(self, engine, mock_collector):
        mock_collector.close_position = MagicMock(return_value=OrderResult(
            order_id="456", symbol="BTC/USDT:USDT", side="sell",
            type="market", amount=0.5, price=None, filled=0.5,
            status="closed", average=50000.0, position_side="LONG",
        ))

        result = engine.close_signal_position("BTC/USDT:USDT", "LONG", 0.5)

        assert result.type == "market"
        assert result.side == "sell"
        mock_collector.close_position.assert_called_once_with(
            symbol="BTC/USDT:USDT", amount=0.5, position_side="LONG",
        )

    def test_close_short_uses_buy(self, engine, mock_collector):
        mock_collector.close_position = MagicMock(return_value=OrderResult(
            order_id="789", symbol="ETH/USDT:USDT", side="buy",
            type="market", amount=1.0, price=None, filled=1.0,
            status="closed", average=3000.0, position_side="SHORT",
        ))

        result = engine.close_signal_position("ETH/USDT:USDT", "SHORT", 1.0)

        assert result.side == "buy"
        assert result.type == "market"


class TestRiskManager:
    def test_can_trade_ok(self):
        mock_collector = MagicMock()
        mock_collector.fetch_account_info.return_value = MagicMock(
            available_balance=1000.0, account_status="NORMAL"
        )
        rm = RiskManager()
        ok, msg = rm.check_can_trade(mock_collector)
        assert ok is True
        assert "OK" in msg

    def test_insufficient_balance(self):
        mock_collector = MagicMock()
        mock_collector.fetch_account_info.return_value = MagicMock(
            available_balance=1.0, account_status="NORMAL"
        )
        rm = RiskManager(min_notional=5.50)
        ok, msg = rm.check_can_trade(mock_collector)
        assert ok is False
        assert "Insufficient" in msg

    def test_abnormal_account(self):
        mock_collector = MagicMock()
        mock_collector.fetch_account_info.return_value = MagicMock(
            available_balance=1000.0, account_status="LIQUIDATION"
        )
        rm = RiskManager()
        ok, msg = rm.check_can_trade(mock_collector)
        assert ok is False
        assert "LIQUIDATION" in msg
