"""Signal detection: threshold check, liquidity filter, ranking, TG integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fund_rate_arb.config import DEFAULT_FEES
from fund_rate_arb.models.funding import FundingRate, SpreadData
from fund_rate_arb.scoring.fee_model import annualized_funding_apy

logger = logging.getLogger(__name__)

BINANCE_MAKER = DEFAULT_FEES["binance_maker"]
BINANCE_TAKER = DEFAULT_FEES["binance_taker"]
HL_MAKER = DEFAULT_FEES["hyperliquid_maker"]
HL_TAKER = DEFAULT_FEES["hyperliquid_taker"]


@dataclass
class Signal:
    exchange: str
    symbol: str
    apy_net: float
    apy_gross: float
    cost: float
    basis_pct: float
    spread_bps: float
    interval_h: int
    # 72h history
    avg_rate_72h: float = 0.0
    std_rate_72h: float = 0.0
    positive_ratio_72h: float = 0.0
    # Ranking scores
    score_daily: float = 0.0
    score_weekly: float = 0.0


def calc_round_trip_cost(maker: float, taker: float) -> float:
    return (maker + taker) * 2


def calc_cost_pct(spread_bps: float, maker: float, taker: float) -> float:
    """Return one-way cost as percentage (e.g., 0.15 = 0.15%)."""
    rt_cost = calc_round_trip_cost(maker, taker) * 100
    spread_pct = spread_bps / 100
    return rt_cost + spread_pct


def _apy_to_pct(apy_decimal: float) -> float:
    """Convert APY decimal to percentage (0.3285 -> 32.85)."""
    return apy_decimal * 100


def detect_signals(
    funding_rates: list[FundingRate],
    spreads: list[SpreadData],
    apy_threshold: float = 15.0,
    max_spread_bps: float = 10.0,
) -> list[Signal]:
    spread_map = {(s.exchange, s.symbol): s for s in spreads}
    signals = []

    for fr in funding_rates:
        key = (fr.exchange, fr.symbol)
        spread = spread_map.get(key)
        if not spread:
            continue

        if spread.spread_bps > max_spread_bps:
            continue

        maker = BINANCE_MAKER if fr.exchange == "binance" else HL_MAKER
        taker = BINANCE_TAKER if fr.exchange == "binance" else HL_TAKER
        interval_h = 8 if fr.exchange == "binance" else 1

        intervals_per_year = 365 * 3 if fr.exchange == "binance" else 365 * 24
        apy_gross_decimal = annualized_funding_apy(
            fr.funding_rate, intervals_per_year=intervals_per_year,
        )
        apy_gross = _apy_to_pct(apy_gross_decimal)
        cost = calc_cost_pct(spread.spread_bps, maker, taker)
        apy_net = apy_gross - cost

        if apy_net >= apy_threshold:
            basis = _calc_basis(fr, spread)
            sig = Signal(
                exchange="BN" if fr.exchange == "binance" else "HL",
                symbol=fr.symbol.removesuffix("USDT"),
                apy_net=round(apy_net, 2),
                apy_gross=round(apy_gross, 2),
                cost=round(cost, 2),
                basis_pct=round(basis, 4),
                spread_bps=round(spread.spread_bps, 1),
                interval_h=interval_h,
            )
            signals.append(sig)

    return signals


def rank_signals(
    signals: list[Signal],
    history_map: dict[tuple[str, str], list[float]],
) -> list[Signal]:
    """Enrich signals with 72h history and compute ranking scores.

    Scoring model:
      score = net_apy * persistence * (1 - volatility_penalty)
        - spread_cost is annualized based on rebalance frequency
        - daily   rebalance: spread_pct × 365  → subtracted from gross
        - weekly  rebalance: spread_pct × 52   → subtracted from gross
        - persistence: positive_ratio over 72h (>0.6 → bonus, <0.4 → penalty)
        - volatility_penalty: std/abs(avg) capped at 0.5
    """
    for s in signals:
        key = (s.exchange, s.symbol)
        rates = history_map.get(key, [])

        if len(rates) >= 2:
            import statistics
            s.avg_rate_72h = round(statistics.mean(rates), 8)
            s.std_rate_72h = round(statistics.stdev(rates), 8)
            s.positive_ratio_72h = round(
                sum(1 for r in rates if r > 0) / len(rates), 4,
            )
        else:
            s.avg_rate_72h = 0.0
            s.std_rate_72h = 0.0
            s.positive_ratio_72h = 0.5  # neutral if no history

        # Spread cost annualized for each rebalance frequency
        spread_pct = s.spread_bps / 100  # e.g. 5.7 bps → 0.057%
        cost_daily = spread_pct * 365    # daily rebalance
        cost_weekly = spread_pct * 52    # weekly rebalance

        net_daily = s.apy_gross - cost_daily
        net_weekly = s.apy_gross - cost_weekly

        # Persistence factor: 0.6–1.0 range (below 0.6 hurts, above 0.8 helps)
        persistence = 0.6 + s.positive_ratio_72h * 0.4
        persistence = min(max(persistence, 0.4), 1.0)

        # Volatility penalty: high std relative to avg = less predictable
        if s.avg_rate_72h != 0 and s.std_rate_72h > 0:
            vol_ratio = min(s.std_rate_72h / abs(s.avg_rate_72h), 1.0)
        else:
            vol_ratio = 0.3  # moderate penalty if no history
        vol_penalty = 1.0 - vol_ratio * 0.5  # max 50% reduction

        # Stability bonus: 8h interval is more predictable than 1h
        stability = 1.05 if s.interval_h == 8 else 0.95

        s.score_daily = round(net_daily * persistence * vol_penalty * stability, 2)
        s.score_weekly = round(net_weekly * persistence * vol_penalty * stability, 2)

    return signals


def _calc_basis(fr: FundingRate, spread: SpreadData) -> float:
    """Return basis as percentage."""
    if not fr.mark_price:
        return 0.0
    mid = (spread.bid + spread.ask) / 2
    if mid == 0:
        return 0.0
    return (fr.mark_price - mid) / mid * 100
