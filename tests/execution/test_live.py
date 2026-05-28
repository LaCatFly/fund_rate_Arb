"""LiveExecutor opens/closes real positions via collector."""

import pytest
from unittest.mock import MagicMock
from fund_rate_arb.execution.live import LiveExecutor
from fund_rate_arb.models.funding import CarryPosition
from fund_rate_arb.signal.detector import Signal


@pytest.fixture
def mock_collector():
    c = MagicMock()
    c.exchange_name = "binance"
    c.place_order.return_value = MagicMock(
        order_id="1", symbol="TSM/USDT:USDT", side="sell",
        type="market", amount=0.5, filled=0.5,
        status="closed", average=418.5, position_side="SHORT",
    )
    c.close_position.return_value = MagicMock(
        order_id="2", symbol="TSM/USDT:USDT", side="buy",
        type="market", amount=0.5, filled=0.5,
        status="closed", average=420.0, position_side="SHORT",
    )
    return c


def test_open_position_short(mock_collector):
    executor = LiveExecutor(collector=mock_collector, notional_per_leg=200.0)
    sig = Signal(exchange="binance", symbol="TSM", apy_net=20.0, apy_gross=22.0, cost=0.05,
                 basis_pct=0.001, spread_bps=1.5, interval_h=8,
                 avg_rate_72h=0.0003, std_rate_72h=0.0001, positive_ratio_72h=0.9)
    pos = executor.open_position(sig, mark_price=419.0)
    assert pos is not None
    assert pos.side == "SHORT"
    assert pos.symbol == "TSMUSDT"
    assert mock_collector.place_order.called


def test_open_position_no_mark_price_returns_none(mock_collector):
    executor = LiveExecutor(collector=mock_collector, notional_per_leg=200.0)
    sig = Signal(exchange="binance", symbol="TSM", apy_net=20.0, apy_gross=22.0, cost=0.05,
                 basis_pct=0.001, spread_bps=1.5, interval_h=8,
                 avg_rate_72h=0.0003, std_rate_72h=0.0001, positive_ratio_72h=0.9)
    pos = executor.open_position(sig, mark_price=0)
    assert pos is None


def test_close_position(mock_collector):
    executor = LiveExecutor(collector=mock_collector, notional_per_leg=200.0)
    pos = CarryPosition(
        execution_id="x", strategy_name="funding_carry",
        symbol="TSM/USDT:USDT", exchange="binance", side="SHORT",
        contracts=0.5, entry_price=419.0, entry_basis=0.001,
        entry_cost=0.1, cumulative_funding=0.0, notional_usdt=200.0,
        opened_at="2026-01-01T00:00:00", max_break_even_days=10, status="Open",
    )
    ok = executor.close_position(pos, "time_based")
    assert ok is True
    assert pos.status == "Closed"
    assert pos.close_reason == "time_based"
    mock_collector.close_position.assert_called_once()
