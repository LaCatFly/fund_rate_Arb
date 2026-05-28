"""Tests for paper executor — simulated fills."""

import pytest
from fund_rate_arb.execution.paper import PaperExecutor
from fund_rate_arb.signal.detector import Signal
from fund_rate_arb.models.funding import CarryPosition


@pytest.fixture
def btc_signal():
    return Signal(
        exchange="BN", symbol="BTC", apy_net=25.0, apy_gross=26.0,
        cost=1.0, basis_pct=0.05, spread_bps=2.0, interval_h=8,
    )


class TestPaperExecutor:
    def test_open_position(self, btc_signal):
        ex = PaperExecutor()
        pos = ex.open_position(btc_signal, "test-exec-1", mark_price=50000.0)
        assert pos is not None
        assert pos.symbol == "BTCUSDT"
        assert pos.exchange == "paper"
        assert pos.side == "SHORT"
        assert pos.contracts > 0
        assert pos.entry_price == 50000.0
        assert pos.status == "Open"

    def test_close_position(self, btc_signal):
        ex = PaperExecutor()
        pos = ex.open_position(btc_signal, "test-exec-1", mark_price=50000.0)
        result = ex.close_position(pos, "regime_change")
        assert result is True

    def test_simulated_fill_price(self, btc_signal):
        """Paper fills at mark price with no slippage."""
        ex = PaperExecutor()
        pos = ex.open_position(btc_signal, "test-exec-1", mark_price=50000.0)
        assert pos.entry_price == 50000.0

    def test_notional_calculation(self, btc_signal):
        """Position notional = contracts * entry_price."""
        ex = PaperExecutor(notional_per_leg=100.0)
        pos = ex.open_position(btc_signal, "test-exec-1", mark_price=50000.0)
        expected_contracts = 100.0 / 50000.0  # 0.002
        assert abs(pos.contracts - expected_contracts) < 0.0001
        assert abs(pos.notional_usdt - 100.0) < 0.01
