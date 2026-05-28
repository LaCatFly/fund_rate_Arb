"""Tests for exit rule engine."""

from fund_rate_arb.risk.exit_engine import (
    ExitRuleEngine, TimeBasedRule, FundingFlipRule,
    APYThresholdRule,
)
from fund_rate_arb.models.funding import CarryPosition, MarketData, ExitSignal
from datetime import datetime, timezone, timedelta


def _position(hours_ago: int = 1) -> CarryPosition:
    return CarryPosition(
        execution_id="test-1", strategy_name="funding_carry",
        symbol="BTCUSDT", exchange="binance", side="SHORT",
        contracts=0.01, entry_price=50000.0, entry_basis=0.0001,
        entry_cost=0.5, cumulative_funding=0.0,
        notional_usdt=500.0, opened_at=(datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        max_break_even_days=10, status="Open",
    )


def _market_data(funding_rates: list[float]) -> MarketData:
    return MarketData(
        symbol="BTCUSDT", exchange="binance",
        current_mark=50000.0, current_index=49950.0,
        current_basis=0.001, funding_history_48h=funding_rates,
        oi_window_8h=[1000.0] * 10,
    )


class TestTimeBasedRule:
    def test_exits_after_max_hold_time(self):
        rule = TimeBasedRule(max_hold_hours=24)
        pos = _position(hours_ago=25)
        signals = rule.check(pos, _market_data([0.0001] * 10))
        assert len(signals) == 1
        assert signals[0].severity == "critical"

    def test_no_exit_within_time(self):
        rule = TimeBasedRule(max_hold_hours=24)
        pos = _position(hours_ago=12)
        signals = rule.check(pos, _market_data([0.0001] * 10))
        assert len(signals) == 0


class TestFundingFlipRule:
    def test_exits_on_negative_funding(self):
        rule = FundingFlipRule(consecutive_neg=3)
        pos = _position()
        market = _market_data([-0.0001, -0.0001, -0.0001])
        signals = rule.check(pos, market)
        assert len(signals) == 1
        assert "negative" in signals[0].message.lower()

    def test_no_exit_with_positive_funding(self):
        rule = FundingFlipRule(consecutive_neg=3)
        pos = _position()
        market = _market_data([0.0001, 0.0001, 0.0001])
        signals = rule.check(pos, market)
        assert len(signals) == 0


class TestAPYThresholdRule:
    def test_exits_when_apy_drops_below(self):
        rule = APYThresholdRule(min_apy=10.0)
        pos = _position()
        market = _market_data([0.00001] * 10)
        signals = rule.check(pos, market)
        assert len(signals) == 1

    def test_no_exit_when_apy_sufficient(self):
        rule = APYThresholdRule(min_apy=10.0)
        pos = _position()
        market = _market_data([0.0001] * 10)
        signals = rule.check(pos, market)
        assert len(signals) == 0


class TestExitRuleEngine:
    def test_multiple_rules_aggregate(self):
        engine = ExitRuleEngine([
            TimeBasedRule(max_hold_hours=24),
            FundingFlipRule(consecutive_neg=3),
        ])
        pos = _position(hours_ago=25)
        market = _market_data([-0.0001] * 3)
        signals = engine.check_all(pos, market)
        assert len(signals) == 2

    def test_no_signals_when_all_clear(self):
        engine = ExitRuleEngine([
            TimeBasedRule(max_hold_hours=24),
            FundingFlipRule(consecutive_neg=3),
        ])
        pos = _position(hours_ago=1)
        market = _market_data([0.0001] * 10)
        signals = engine.check_all(pos, market)
        assert len(signals) == 0
