"""Per-strategy configuration models and builders."""

from __future__ import annotations

from pydantic import BaseModel

from fund_rate_arb.execution.allocator import Allocator
from fund_rate_arb.risk.exit_engine import (
    APYThresholdRule,
    ExitRule as ExitRuleABC,
    ExitRuleEngine,
    FundingFlipRule,
    MaxLossRule,
    TimeBasedRule,
)


class ExecutionConfig(BaseModel):
    notional_per_leg: float = 200.0
    max_retries: int = 5
    retry_delay_s: float = 2.0
    leverage: int = 1


class ExitRule(BaseModel):
    type: str
    params: dict


class AnomalyConfig(BaseModel):
    oi_window_hours: int = 8
    oi_change_threshold_pct: float = 20.0
    funding_stdev_lookback_days: int = 30
    funding_stdev_threshold: float = 2.0


class SelectionConfig(BaseModel):
    top_fraction: float = 0.10
    max_concurrent_positions: int = 5
    min_apy_threshold: float = 10.0
    min_oi_usd: float = 5_000_000


class StrategySpec(BaseModel):
    name: str
    enabled: bool = True
    polling_interval_s: int = 3600
    weights: dict[str, float]
    execution: ExecutionConfig = ExecutionConfig()
    selection: SelectionConfig = SelectionConfig()
    exit_rules: list[ExitRule] = []
    anomaly_detection: AnomalyConfig = AnomalyConfig()


def parse_strategy_specs(raw: list[dict]) -> list[StrategySpec]:
    return [StrategySpec(**entry) for entry in raw]


# ---------------------------------------------------------------------------
# Exit rule factory — YAML type → concrete rule instance
# ---------------------------------------------------------------------------

_RULE_REGISTRY: dict[str, type[ExitRuleABC]] = {
    "time_based": TimeBasedRule,
    "funding_flip": FundingFlipRule,
    "apy_threshold": APYThresholdRule,
    "max_loss": MaxLossRule,
}


def build_exit_rules(rules: list[ExitRule]) -> ExitRuleEngine:
    """Convert YAML exit rules into ExitRuleEngine."""
    instances = []
    for rule in rules:
        cls = _RULE_REGISTRY.get(rule.type)
        if cls is None:
            raise ValueError(f"Unknown exit rule type: {rule.type!r}")
        instances.append(cls(**rule.params))
    return ExitRuleEngine(instances)


# ---------------------------------------------------------------------------
# Strategy builder — StrategySpec → configured FundingCarry
# ---------------------------------------------------------------------------


def build_funding_carry(
    spec: StrategySpec,
    paper: bool = True,
    db_path: str = "fund_rate_arb.db",
    perp_executor=None,
    spot_executor=None,
) -> "FundingCarry":
    """Build a FundingCarry strategy from a StrategySpec.

    Pass perp_executor/spot_executor to override the default executor creation
    (useful for tests or when live collectors need custom setup).
    """
    from fund_rate_arb.execution.paper import PaperExecutor

    notional = spec.execution.notional_per_leg
    max_pos = spec.selection.max_concurrent_positions
    min_apy = spec.selection.min_apy_threshold

    if perp_executor is None:
        perp_executor = PaperExecutor(notional_per_leg=notional)
    if spot_executor is None:
        spot_executor = PaperExecutor(notional_per_leg=notional)

    exit_engine = build_exit_rules(spec.exit_rules)

    allocator = Allocator(
        total_capital=notional * max_pos * 2,
        max_concurrent=max_pos,
        notional_per_leg=notional * 2,
    )

    from fund_rate_arb.strategies.funding_carry import FundingCarry

    return FundingCarry(
        perp_executor=perp_executor,
        spot_executor=spot_executor,
        exit_engine=exit_engine,
        max_positions=max_pos,
        min_apy=min_apy,
        db_path=db_path,
        notional_per_leg=notional,
        allocator=allocator,
        min_oi_usd=spec.selection.min_oi_usd,
    )
