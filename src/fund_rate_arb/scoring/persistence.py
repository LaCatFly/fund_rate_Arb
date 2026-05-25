"""Persistence analysis for funding rates."""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class PersistenceResult:
    positive_ratio: float       # positive_intervals / total_intervals
    mean: float                 # average funding rate
    std: float                  # std dev of funding rate
    regime: str                 # bull/bear/neutral
    consecutive_positive: int   # longest streak of positive funding
    sample_count: int


def analyze_persistence(
    funding_rates: list[float],
    threshold: float = 0.0,
) -> PersistenceResult:
    """Analyze funding rate persistence from historical data.

    Args:
        funding_rates: List of historical funding rates (oldest first).
        threshold: Rate above which counts as "positive".

    Returns:
        PersistenceResult with ratio, mean, volatility, regime.
    """
    if not funding_rates:
        return PersistenceResult(0.0, 0.0, 0.0, "neutral", 0, 0)

    positive = sum(1 for r in funding_rates if r > threshold)
    total = len(funding_rates)
    positive_ratio = positive / total if total > 0 else 0.0

    mean = statistics.mean(funding_rates)
    std = statistics.stdev(funding_rates) if total > 1 else 0.0

    # Longest consecutive positive streak
    max_streak = 0
    current_streak = 0
    for r in funding_rates:
        if r > threshold:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    # Regime detection based on mean and positive ratio
    if mean > 0.0001 and positive_ratio > 0.6:
        regime = "bull"  # structurally positive funding
    elif mean < -0.0001 and positive_ratio < 0.4:
        regime = "bear"  # structurally negative
    else:
        regime = "neutral"

    return PersistenceResult(
        positive_ratio=round(positive_ratio, 4),
        mean=round(mean, 8),
        std=round(std, 8),
        regime=regime,
        consecutive_positive=max_streak,
        sample_count=total,
    )
