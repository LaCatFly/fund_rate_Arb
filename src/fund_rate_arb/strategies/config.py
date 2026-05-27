"""Per-strategy configuration models."""

from __future__ import annotations

from pydantic import BaseModel


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
