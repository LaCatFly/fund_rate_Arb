"""Signal detection: threshold check, liquidity filter, TG integration."""

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


def calc_round_trip_cost(maker: float, taker: float) -> float:
    return (maker + taker) * 2


def calc_cost_pct(spread_bps: float, maker: float, taker: float) -> float:
    """Return cost as percentage (e.g., 0.15 = 0.15%)."""
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

        apy_gross_decimal = annualized_funding_apy(fr.funding_rate, intervals_per_year=365 * 3 if fr.exchange == "binance" else 365 * 24)
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
            logger.info("Signal: %s %s APY %.1f%%", sig.exchange, sig.symbol, sig.apy_net)

    return signals


def _calc_basis(fr: FundingRate, spread: SpreadData) -> float:
    """Return basis as percentage."""
    if not fr.mark_price:
        return 0.0
    mid = (spread.bid + spread.ask) / 2
    if mid == 0:
        return 0.0
    return (fr.mark_price - mid) / mid * 100
