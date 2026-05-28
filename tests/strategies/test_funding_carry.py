"""Tests for FundingCarry strategy."""

import pytest
from fund_rate_arb.strategies.funding_carry import FundingCarry
from fund_rate_arb.signal.detector import Signal
from fund_rate_arb.models.funding import CarryPosition
from fund_rate_arb.execution.paper import PaperExecutor
from fund_rate_arb.risk.exit_engine import ExitRuleEngine, TimeBasedRule


@pytest.fixture
def high_apy_signal():
    sig = Signal(
        exchange="BN", symbol="BTC", apy_net=25.0, apy_gross=26.0,
        cost=1.0, basis_pct=0.05, spread_bps=2.0, interval_h=8,
    )
    sig._mark_price = 50000.0
    return sig


@pytest.fixture
def low_apy_signal():
    return Signal(
        exchange="BN", symbol="ETH", apy_net=5.0, apy_gross=6.0,
        cost=1.0, basis_pct=0.02, spread_bps=1.0, interval_h=8,
    )


@pytest.fixture
def strategy():
    return FundingCarry(
        executor=PaperExecutor(notional_per_leg=200.0),
        exit_engine=ExitRuleEngine([TimeBasedRule(max_hold_hours=168)]),
        max_positions=3,
        min_apy=15.0,
    )


class TestSelection:
    def test_selects_above_threshold(self, strategy, high_apy_signal):
        result = strategy.select([high_apy_signal], [])
        assert len(result) == 1

    def test_filters_below_threshold(self, strategy, low_apy_signal):
        result = strategy.select([low_apy_signal], [])
        assert len(result) == 0

    def test_respects_max_positions(self, strategy, high_apy_signal):
        open_pos = [
            CarryPosition(
                execution_id=f"p{i}", strategy_name="funding_carry",
                symbol=f"SYM{i}USDT", exchange="paper", side="SHORT",
                contracts=0.01, entry_price=100.0, entry_basis=0,
                entry_cost=0, cumulative_funding=0, notional_usdt=100,
                opened_at="2026-01-01T00:00:00", max_break_even_days=10,
                status="Open",
            )
            for i in range(3)
        ]
        result = strategy.select([high_apy_signal], open_pos)
        assert len(result) == 0


class TestOpenPosition:
    @pytest.mark.asyncio
    async def test_opens_short(self, strategy, high_apy_signal):
        pos = await strategy.open_position(high_apy_signal, "test.db")
        assert pos is not None
        assert pos.side == "SHORT"
        assert pos.exchange == "paper"
        assert pos.status == "Open"
