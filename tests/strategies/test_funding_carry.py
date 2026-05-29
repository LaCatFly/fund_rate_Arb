"""Tests for FundingCarry paired strategy."""

import pytest
from unittest.mock import MagicMock
from fund_rate_arb.strategies.funding_carry import FundingCarry
from fund_rate_arb.signal.detector import Signal
from fund_rate_arb.models.funding import CarryPosition
from fund_rate_arb.risk.exit_engine import ExitRuleEngine, TimeBasedRule
from fund_rate_arb.config import Underlying


@pytest.fixture
def btc_underlying():
    return Underlying(
        ticker="BTC", name="Bitcoin",
        binance_f="BTCUSDT",
        hl_perp="BTC", hl_spot="BTC",
        sector="crypto_perp",
    )


@pytest.fixture
def high_apy_signal():
    sig = Signal(
        exchange="BN",
        symbol="BTC",
        apy_net=25.0,
        apy_gross=26.0,
        cost=1.0,
        basis_pct=0.05,
        spread_bps=2.0,
        interval_h=8,
    )
    sig._mark_price = 50000.0
    return sig


@pytest.fixture
def low_apy_signal():
    return Signal(
        exchange="BN",
        symbol="ETH",
        apy_net=5.0,
        apy_gross=6.0,
        cost=1.0,
        basis_pct=0.02,
        spread_bps=1.0,
        interval_h=8,
    )


@pytest.fixture
def mock_perp_executor():
    ex = MagicMock()
    ex.exchange_name = "binance"
    ex.open_position.return_value = MagicMock(
        execution_id="1_perp", symbol="BTCUSDT", side="SHORT",
        contracts=0.0015, entry_price=50000.0, status="open",
    )
    ex.close_position.return_value = MagicMock(status="closed")
    return ex


@pytest.fixture
def mock_spot_executor():
    ex = MagicMock()
    ex.exchange_name = "binance_spot"
    ex.open_position.return_value = MagicMock(
        execution_id="1_spot", symbol="BTCUSDT", side="LONG",
        contracts=0.0015, entry_price=50000.0, status="open",
    )
    ex.close_position.return_value = MagicMock(status="closed")
    return ex


@pytest.fixture
def strategy(mock_perp_executor, mock_spot_executor):
    return FundingCarry(
        perp_executor=mock_perp_executor,
        spot_executor=mock_spot_executor,
        exit_engine=ExitRuleEngine([TimeBasedRule(max_hold_hours=168)]),
        max_positions=6,
        min_apy=15.0,
        notional_per_leg=75.0,
    )


class TestSelection:
    def test_selects_underlying_with_both_perp_and_spot(self, strategy, btc_underlying, high_apy_signal):
        # BTC has both binance_f and binance_s
        from fund_rate_arb import config
        original = config.UNDERLYINGS
        config.UNDERLYINGS = [btc_underlying]
        config._UNDERLYING_BY_TICKER = {u.ticker: u for u in config.UNDERLYINGS}
        try:
            result = strategy.select([high_apy_signal], [])
            assert len(result) == 1
            assert result[0].ticker == "BTC"
        finally:
            config.UNDERLYINGS = original
            config._UNDERLYING_BY_TICKER = {u.ticker: u for u in original}

    def test_filters_underlying_without_spot(self, strategy, low_apy_signal):
        """Underlying with only perp (no spot) is excluded."""
        no_spot = Underlying(
            ticker="TSM", name="TSMC",
            binance_f=None,
            hl_perp=None, hl_spot=None,
            sector="equity",
        )
        from fund_rate_arb import config
        original = config.UNDERLYINGS
        config.UNDERLYINGS = [no_spot]
        config._UNDERLYING_BY_TICKER = {u.ticker: u for u in config.UNDERLYINGS}
        try:
            result = strategy.select([low_apy_signal], [])
            assert len(result) == 0  # No spot = no pair
        finally:
            config.UNDERLYINGS = original
            config._UNDERLYING_BY_TICKER = {u.ticker: u for u in original}

    def test_respects_max_positions(self, strategy, btc_underlying, high_apy_signal):
        open_pos = [
            CarryPosition(
                execution_id=f"p{i}",
                strategy_name="funding_carry",
                symbol=f"SYM{i}/USDT",
                exchange="binance",
                side="NEUTRAL",
                contracts=0.01,
                entry_price=100.0,
                entry_basis=0,
                entry_cost=0,
                cumulative_funding=0,
                notional_usdt=100,
                opened_at="2026-01-01T00:00:00",
                max_break_even_days=10,
                status="Open",
            )
            for i in range(6)  # 6 positions = allocator full
        ]
        strategy._sync_allocator(open_pos)
        from fund_rate_arb import config
        original = config.UNDERLYINGS
        config.UNDERLYINGS = [btc_underlying]
        config._UNDERLYING_BY_TICKER = {u.ticker: u for u in config.UNDERLYINGS}
        try:
            result = strategy.select([high_apy_signal], open_pos)
            assert len(result) == 0
        finally:
            config.UNDERLYINGS = original
            config._UNDERLYING_BY_TICKER = {u.ticker: u for u in original}


class TestOpenPairedPosition:
    @pytest.mark.asyncio
    async def test_opens_both_legs(self, strategy, btc_underlying, monkeypatch):
        """Both perp and spot legs are opened."""
        monkeypatch.setattr(strategy, "_get_mark_price", lambda sym, db: 50000.0)
        pos = await strategy.open_paired_position(btc_underlying, "test.db")
        assert pos is not None
        assert strategy.perp_executor.open_position.called
        assert strategy.spot_executor.open_position.called

    @pytest.mark.asyncio
    async def test_returns_none_on_no_mark_price(self, strategy, btc_underlying, monkeypatch):
        """No mark price returns None."""
        monkeypatch.setattr(strategy, "_get_mark_price", lambda sym, db: 0.0)
        pos = await strategy.open_paired_position(btc_underlying, "test.db")
        assert pos is None
