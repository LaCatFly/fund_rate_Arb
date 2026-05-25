"""Fee model and break-even calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeeBreakdown:
    maker_fee: float        # per side
    taker_fee: float        # per side
    slippage: float         # estimated entry+exit
    spread_cost: float      # bid-ask cost
    total_entry: float      # one-way cost
    total_round_trip: float # entry + exit
    break_even_days: float  # days to recover costs


def compute_fees(
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
    slippage: float = 0.0003,
    spread_cost: float = 0.0002,
    net_funding_per_day: float = 0.0,
) -> FeeBreakdown:
    """Compute round-trip fees and break-even holding period.

    Args:
        maker_fee: Maker fee rate (0.02% = 0.0002)
        taker_fee: Taker fee rate (0.05% = 0.0005)
        slippage: Estimated slippage on entry+exit
        spread_cost: Bid-ask spread cost
        net_funding_per_day: Expected daily net funding income

    Returns:
        FeeBreakdown with total costs and break-even days.
    """
    # Entry: maker on one leg, possibly taker on other
    entry_cost = (maker_fee + taker_fee) / 2 + slippage + spread_cost / 2
    # Exit: same structure
    exit_cost = entry_cost
    total_round_trip = entry_cost + exit_cost

    if net_funding_per_day > 0:
        break_even_days = total_round_trip / net_funding_per_day
    else:
        break_even_days = float("inf")

    return FeeBreakdown(
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        slippage=slippage,
        spread_cost=spread_cost,
        total_entry=round(entry_cost, 6),
        total_round_trip=round(total_round_trip, 6),
        break_even_days=round(break_even_days, 1) if break_even_days != float("inf") else -1.0,
    )


def annualized_funding_apy(
    funding_rate_per_interval: float,
    intervals_per_year: int = 1095,  # 3 * 365
) -> float:
    """Convert per-interval funding rate to annualized percentage.

    Args:
        funding_rate_per_interval: e.g. 0.0001 = 0.01% per 8h
        intervals_per_year: Default 1095 (Binance 8h intervals)

    Returns:
        Annualized APY as decimal (e.g. 0.1095 = 10.95%)
    """
    return funding_rate_per_interval * intervals_per_year
