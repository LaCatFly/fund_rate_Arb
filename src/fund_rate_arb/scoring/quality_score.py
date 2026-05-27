"""Quality scoring engine for funding rate arbitrage."""

from __future__ import annotations

import statistics

from fund_rate_arb.config import Config, DEFAULT_WEIGHTS, DEFAULT_FEES
from fund_rate_arb.models.funding import FundingScore
from fund_rate_arb.scoring.persistence import PersistenceResult, analyze_persistence
from fund_rate_arb.scoring.fee_model import compute_fees, annualized_funding_apy


def compute_quality_score(
    symbol: str,
    exchange: str,
    funding_history: list[float],
    oi_history: list[float] | None = None,
    spread_bps: float = 0.0,
    weights: dict[str, float] | None = None,
    fees: dict[str, float] | None = None,
) -> FundingScore:
    """Compute comprehensive quality score for a funding rate opportunity.

    Score = w1 * Funding Mean
          + w2 * Persistence
          - w3 * Volatility
          + w4 * OI Stability
          - w5 * Spread Cost
          - w6 * Slippage

    Args:
        funding_history: Historical funding rates (oldest first).
        oi_history: Historical OI values (optional).
        spread_bps: Current bid-ask spread in basis points.
        weights: Scoring weights (uses defaults if None).
        fees: Fee configuration (uses defaults if None).

    Returns:
        FundingScore with all components and estimated APY.
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS
    f = fees if fees is not None else DEFAULT_FEES

    # Persistence analysis
    persist = analyze_persistence(funding_history)

    # Normalized components (scale to 0-1 range where possible)
    # Funding mean: normalize by dividing by typical max (0.001 = 0.1% per interval)
    funding_mean_norm = min(abs(persist.mean) / 0.001, 1.0)
    if persist.mean < 0:
        funding_mean_norm = -funding_mean_norm  # negative funding = bad for carry

    # Persistence ratio is already 0-1
    persistence_norm = persist.positive_ratio

    # Volatility: normalize by dividing by typical max (0.0005)
    volatility_norm = min(persist.std / 0.0005, 1.0)

    # OI stability: coefficient of variation, inverted (lower CV = more stable = better)
    oi_stability_norm = 0.5  # default if no data
    if oi_history and len(oi_history) > 1:
        oi_mean = statistics.mean(oi_history)
        oi_std = statistics.stdev(oi_history)
        if oi_mean > 0:
            cv = oi_std / oi_mean
            oi_stability_norm = max(0.0, 1.0 - cv)

    # Spread cost: normalize (lower spread = better)
    spread_cost_norm = min(spread_bps / 10.0, 1.0)  # 10 bps = max penalty

    # Slippage: normalized (assume proportional to spread)
    slippage_norm = spread_cost_norm * 0.5

    # Weighted score (weights are negative for penalties, applied directly)
    score = (
        w["funding_mean"] * funding_mean_norm
        + w["persistence"] * persistence_norm
        + w["volatility"] * volatility_norm
        + w["oi_stability"] * oi_stability_norm
        + w["spread_cost"] * spread_cost_norm
        + w["slippage"] * slippage_norm
    )

    # Fee-adjusted APY
    mean_funding = persist.mean
    est_apy = annualized_funding_apy(mean_funding)

    # Break-even calculation
    fee_breakdown = compute_fees(
        maker_fee=f["binance_maker"],
        taker_fee=f["binance_taker"],
        slippage=f["slippage"],
        spread_cost=f["spread_cost"],
        net_funding_per_day=mean_funding * 3,  # 3 intervals per day
    )

    return FundingScore(
        symbol=symbol,
        exchange=exchange,
        score=round(score, 4),
        funding_mean=round(persist.mean, 8),
        persistence=round(persist.positive_ratio, 4),
        volatility=round(persist.std, 8),
        oi_stability=round(oi_stability_norm, 4),
        spread_cost_bps=round(spread_bps, 2),
        estimated_apy=round(est_apy, 4),
        break_even_days=fee_breakdown.break_even_days,
        regime=persist.regime,
    )
