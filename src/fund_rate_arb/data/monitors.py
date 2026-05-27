"""Sliding window monitors for anomaly detection."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class MonitorResult:
    triggered: bool
    metric: str
    current_value: float
    threshold: float
    message: str


def detect_oi_spike(oi_window: list[float], threshold_pct: float = 20.0) -> MonitorResult:
    """Detect OI spike: |oi_now - oi_ago| / oi_ago > threshold."""
    if len(oi_window) < 2:
        return MonitorResult(False, "oi_spike", 0.0, threshold_pct, "insufficient data")
    old = oi_window[0]
    new = oi_window[-1]
    if old == 0:
        return MonitorResult(False, "oi_spike", 0.0, threshold_pct, "zero baseline")
    change_pct = abs(new - old) / old * 100
    return MonitorResult(
        triggered=change_pct > threshold_pct,
        metric="oi_spike",
        current_value=change_pct,
        threshold=threshold_pct,
        message=f"OI changed {change_pct:.1f}% (threshold {threshold_pct:.1f}%)",
    )


def detect_funding_regime_shift(
    current_window: list[float],
    baseline_window: list[float],
    stdev_multiplier: float = 2.0,
) -> MonitorResult:
    """Detect regime shift: std(current) / std(baseline) > multiplier."""
    if len(current_window) < 2 or len(baseline_window) < 2:
        return MonitorResult(False, "regime_shift", 0.0, stdev_multiplier, "insufficient data")
    current_std = _std(current_window)
    baseline_std = _std(baseline_window)
    if baseline_std == 0:
        return MonitorResult(False, "regime_shift", 0.0, stdev_multiplier, "zero baseline std")
    ratio = current_std / baseline_std
    return MonitorResult(
        triggered=ratio > stdev_multiplier,
        metric="regime_shift",
        current_value=ratio,
        threshold=stdev_multiplier,
        message=f"Stdev ratio {ratio:.2f}x (threshold {stdev_multiplier:.1f}x)",
    )


def compute_funding_zscore(current: float, window: list[float]) -> float:
    """z = (current - mean) / std. |z| > 3 = outlier."""
    if len(window) < 2:
        return 0.0
    mean = sum(window) / len(window)
    std = _std(window)
    if std == 0:
        return 0.0
    return (current - mean) / std


def compute_ewma(values: list[float], span: int = 12) -> float:
    """Exponential weighted moving average for funding rate smoothing."""
    if not values:
        return 0.0
    alpha = 2.0 / (span + 1)
    ewma = values[0]
    for v in values[1:]:
        ewma = alpha * v + (1 - alpha) * ewma
    return ewma


def compute_basis_drift(current_mark: float, current_index: float, entry_basis: float) -> float:
    """abs((mark - index) / index - entry_basis)."""
    if current_index == 0:
        return 0.0
    current_basis = (current_mark - current_index) / current_index
    return abs(current_basis - entry_basis)


def compute_notional_drift(spot_price: float, perp_price: float, entry_notional: float) -> float:
    """abs(spot_notional - perp_notional) / entry_notional. For dual-leg rebalance."""
    if entry_notional == 0:
        return 0.0
    return abs(spot_price - perp_price) / entry_notional


def _std(values: list[float]) -> float:
    """Sample standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)
