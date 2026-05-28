"""ClassicFuturesCollector uses ccxt swap type, no papi."""

import pytest
from unittest.mock import patch, MagicMock
from fund_rate_arb.collectors.classic_futures import ClassicFuturesCollector


def _make_collector():
    """Create collector with mocked exchange to avoid network calls."""
    with patch.dict("os.environ", {"BINANCE_API_KEY": "k", "BINANCE_SECRET": "s"}):
        c = ClassicFuturesCollector()
        mock_ex = MagicMock()
        mock_ex.options = {"defaultType": "swap"}
        c._exchange = mock_ex
        return c, mock_ex


def test_exchange_uses_swap_type():
    c, ex = _make_collector()
    assert c.exchange.options.get("defaultType") == "swap"
    assert "papi" not in c.exchange.options


def test_fetch_positions_filters_empty():
    c, ex = _make_collector()
    ex.fetch_positions.return_value = [
        {"symbol": "TSM/USDT:USDT", "contracts": 4.71, "side": "short",
         "entryPrice": 419.0, "unrealizedPnl": -29.16, "leverage": 1},
        {"symbol": "X/USDT:USDT", "contracts": 0, "side": "long", "entryPrice": 0},
    ]
    positions = c.fetch_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "TSM/USDT:USDT"
    assert positions[0]["contracts"] == 4.71


def test_place_order_passes_position_side():
    c, ex = _make_collector()
    ex.create_order.return_value = {
        "id": "1", "symbol": "T/USDT:USDT", "side": "sell",
        "type": "market", "amount": 1.0, "filled": 1.0,
        "status": "closed", "average": 418.5,
    }
    r = c.place_order("T/USDT:USDT", "sell", 1.0, position_side="SHORT")
    assert r.position_side == "SHORT"
    call_p = ex.create_order.call_args[1]["params"]
    assert call_p["positionSide"] == "SHORT"


def test_close_position_inverts_side():
    c, ex = _make_collector()
    ex.create_order.return_value = {
        "id": "2", "symbol": "T/USDT:USDT", "side": "buy",
        "type": "market", "amount": 1.0, "filled": 1.0,
        "status": "closed", "average": 420.0,
    }
    r = c.close_position("T/USDT:USDT", 1.0, "SHORT")
    assert r.side == "buy"
    assert r.position_side == "SHORT"


def test_fetch_balance_returns_total():
    c, ex = _make_collector()
    ex.fetch_balance.return_value = {"total": {"USDT": 3951.94}}
    balance = c.fetch_balance()
    assert balance["USDT"] == 3951.94


def test_missing_credentials_raises():
    with patch.dict("os.environ", {"BINANCE_API_KEY": "", "BINANCE_SECRET": ""}, clear=False):
        import os
        os.environ.pop("BINANCE_API_KEY", None)
        os.environ.pop("BINANCE_SECRET", None)
        with pytest.raises(ValueError, match="BINANCE_API_KEY"):
            ClassicFuturesCollector()
