"""Strategy framework package."""

from fund_rate_arb.strategies.base import BaseStrategy, StrategyResult
from fund_rate_arb.strategies.config import (
    StrategySpec,
    ExecutionConfig,
    ExitRule,
    build_exit_rules,
    build_funding_carry,
)

__all__ = [
    "BaseStrategy",
    "StrategyResult",
    "StrategySpec",
    "ExecutionConfig",
    "ExitRule",
    "build_exit_rules",
    "build_funding_carry",
]
