"""Strategy framework package."""
from fund_rate_arb.strategies.base import BaseStrategy, StrategyResult
from fund_rate_arb.strategies.config import StrategySpec, ExecutionConfig, ExitRule

__all__ = ["BaseStrategy", "StrategyResult", "StrategySpec", "ExecutionConfig", "ExitRule"]
