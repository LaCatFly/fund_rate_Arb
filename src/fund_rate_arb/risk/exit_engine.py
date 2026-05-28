"""Exit rule engine — checks conditions and generates exit signals."""

from __future__ import annotations

from abc import ABC, abstractmethod

from fund_rate_arb.models.funding import CarryPosition, ExitSignal, MarketData
from fund_rate_arb.scoring.fee_model import annualized_funding_apy


class ExitRule(ABC):
    """Single exit condition."""

    @abstractmethod
    def check(self, position: CarryPosition, market: MarketData) -> list[ExitSignal]: ...


class TimeBasedRule(ExitRule):
    """Exit after maximum holding period."""

    def __init__(self, max_hold_hours: int = 168):  # default 7 days
        self.max_hold_hours = max_hold_hours

    def check(self, position: CarryPosition, market: MarketData) -> list[ExitSignal]:
        from datetime import datetime, timezone

        opened = datetime.fromisoformat(position.opened_at)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        held_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600

        if held_hours >= self.max_hold_hours:
            return [ExitSignal(
                position_execution_id=position.execution_id,
                rule_type="time_based",
                severity="critical",
                message=f"Position held {held_hours:.0f}h, exceeds {self.max_hold_hours}h max",
            )]
        return []


class FundingFlipRule(ExitRule):
    """Exit when funding turns negative for consecutive periods."""

    def __init__(self, consecutive_neg: int = 3):
        self.consecutive_neg = consecutive_neg

    def check(self, position: CarryPosition, market: MarketData) -> list[ExitSignal]:
        recent = market.funding_history_48h[-self.consecutive_neg:]
        if len(recent) < self.consecutive_neg:
            return []

        if all(r < 0 for r in recent):
            return [ExitSignal(
                position_execution_id=position.execution_id,
                rule_type="funding_flip",
                severity="critical",
                message=f"Funding negative for {self.consecutive_neg} consecutive periods",
            )]
        return []


class APYThresholdRule(ExitRule):
    """Exit when net APY drops below minimum."""

    def __init__(self, min_apy: float = 10.0):
        self.min_apy = min_apy

    def check(self, position: CarryPosition, market: MarketData) -> list[ExitSignal]:
        if not market.funding_history_48h:
            return []

        avg_funding = sum(market.funding_history_48h) / len(market.funding_history_48h)
        apy = annualized_funding_apy(avg_funding) * 100  # to percentage

        if apy < self.min_apy:
            return [ExitSignal(
                position_execution_id=position.execution_id,
                rule_type="apy_threshold",
                severity="warning",
                message=f"APY {apy:.1f}% below {self.min_apy}% minimum",
            )]
        return []


class ExitRuleEngine:
    """Aggregates multiple exit rules."""

    def __init__(self, rules: list[ExitRule]):
        self.rules = rules

    def check_all(
        self, position: CarryPosition, market: MarketData,
    ) -> list[ExitSignal]:
        signals = []
        for rule in self.rules:
            signals.extend(rule.check(position, market))
        return signals
