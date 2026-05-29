"""Signal detection: threshold check, liquidity filter, ranking, TG integration."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from fund_rate_arb.config import DEFAULT_FEES, DEFAULT_WEIGHTS
from fund_rate_arb.models.funding import FundingRate, SpreadData
from fund_rate_arb.scoring.fee_model import annualized_funding_apy
from fund_rate_arb.scoring.persistence import analyze_persistence

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
    # OI
    oi_usd: float = 0.0
    # Spot price (Binance Alpha API for equities)
    spot_price: float = 0.0
    # Scoring
    quality_score: float = 0.0      # System 1 normalized score (0-1)
    score_daily: float = 0.0        # APY-based daily score
    score_weekly: float = 0.0       # APY-based weekly score (legacy)
    unified_score: float = 0.0      # Combined: quality * APY * persistence * reliability


def calc_round_trip_cost(maker: float, taker: float) -> float:
    """Round-trip cost as decimal. Entry (maker) + exit (taker)."""
    return maker + taker


def calc_cost_pct(spread_bps: float, maker: float, taker: float) -> float:
    """Return total entry+exit cost as percentage (e.g., 0.15 = 0.15%)."""
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
    min_oi_usd: float = 5_000_000,
    oi_map: dict[str, float] | None = None,
) -> list[Signal]:
    spread_map = {(s.exchange, s.symbol): s for s in spreads}
    signals = []

    for fr in funding_rates:
        key = (fr.exchange, fr.symbol)
        spread = spread_map.get(key)
        if not spread:
            continue

        # OI filter — oi_map stores contract counts; convert to USD via mark_price
        oi_symbol = fr.symbol.removesuffix("USDT")
        if oi_map is not None:
            oi_contracts = oi_map.get(oi_symbol, 0) or 0
            oi_value = oi_contracts * (fr.mark_price or 0)
            if oi_value < min_oi_usd:
                continue
        else:
            oi_value = 0

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

        if apy_net >= apy_threshold and spread.spread_bps <= max_spread_bps:
            # Get spot price if available (Binance Alpha API for equities)
            spot = oi_map.get(f"spot:{oi_symbol}", 0) if oi_map else 0
            basis = _calc_basis(fr, spread, spot_price=spot)
            sig = Signal(
                exchange="BN" if fr.exchange == "binance" else "HL",
                symbol=fr.symbol.removesuffix("USDT"),
                apy_net=round(apy_net, 2),
                apy_gross=round(apy_gross, 2),
                cost=round(cost, 2),
                basis_pct=round(basis, 4),
                spread_bps=round(spread.spread_bps, 1),
                interval_h=interval_h,
                oi_usd=oi_value,
            )
            signals.append(sig)

    return signals


def rank_signals(
    signals: list[Signal],
    history_map: dict[tuple[str, str], list[float]],
    oi_map: dict[str, list[float]] | None = None,
    weights: dict[str, float] | None = None,
) -> list[Signal]:
    """Enrich signals with 72h history and compute unified ranking scores.

    Unified scoring:
      quality_score = System 1 weighted formula (normalized 0-1)
      unified_score = quality_score * net_apy * persistence * volatility_penalty * stability

    This merges the normalized quality assessment with absolute return potential.
    """
    w = weights if weights is not None else DEFAULT_WEIGHTS

    for s in signals:
        key = (s.exchange, s.symbol)
        rates = history_map.get(key, [])

        if len(rates) >= 2:
            s.avg_rate_72h = round(statistics.mean(rates), 8)
            s.std_rate_72h = round(statistics.stdev(rates), 8)
            s.positive_ratio_72h = round(
                sum(1 for r in rates if r > 0) / len(rates), 4,
            )
        else:
            s.avg_rate_72h = 0.0
            s.std_rate_72h = 0.0
            s.positive_ratio_72h = 0.5  # neutral if no history

        # --- System 1: Normalized quality score ---
        persist = analyze_persistence(rates if rates else [0.0])

        funding_mean_norm = min(abs(persist.mean) / 0.001, 1.0)
        if persist.mean < 0:
            funding_mean_norm = -funding_mean_norm

        persistence_norm = persist.positive_ratio
        volatility_norm = min(persist.std / 0.0005, 1.0)

        # OI stability from historical OI data
        oi_stability_norm = 0.5  # default
        if oi_map is not None:
            oi_symbol = key[1]
            oi_hist = oi_map.get(oi_symbol, [])
            if len(oi_hist) > 1:
                oi_mean = statistics.mean(oi_hist)
                oi_std = statistics.stdev(oi_hist)
                if oi_mean > 0:
                    cv = oi_std / oi_mean
                    oi_stability_norm = max(0.0, 1.0 - cv)

        spread_cost_norm = min(s.spread_bps / 10.0, 1.0)
        slippage_norm = spread_cost_norm * 0.5

        s.quality_score = (
            w["funding_mean"] * funding_mean_norm
            + w["persistence"] * persistence_norm
            + w["volatility"] * volatility_norm
            + w["oi_stability"] * oi_stability_norm
            + w["spread_cost"] * spread_cost_norm
            + w["slippage"] * slippage_norm
        )

        # --- System 2: APY-based score (legacy) ---
        spread_pct = s.spread_bps / 100  # 5.7 bps -> 0.057%
        cost_daily = spread_pct * 365    # daily rebalance cost
        cost_weekly = spread_pct * 52    # weekly rebalance cost

        net_daily = s.apy_gross - cost_daily
        net_weekly = s.apy_gross - cost_weekly

        # Persistence factor: 0.4-1.0 range
        persistence = 0.6 + s.positive_ratio_72h * 0.4
        persistence = min(max(persistence, 0.4), 1.0)

        # Volatility penalty: high std relative to avg = less predictable
        if s.avg_rate_72h != 0 and s.std_rate_72h > 0:
            vol_ratio = min(s.std_rate_72h / abs(s.avg_rate_72h), 1.0)
        else:
            vol_ratio = 0.3
        vol_penalty = 1.0 - vol_ratio * 0.5  # max 50% reduction

        # Stability bonus: 8h interval is more predictable than 1h
        stability = 1.05 if s.interval_h == 8 else 0.95

        s.score_daily = round(net_daily * persistence * vol_penalty * stability, 2)
        s.score_weekly = round(net_weekly * persistence * vol_penalty * stability, 2)

        # --- Unified score: quality * APY * persistence * reliability ---
        quality_factor = max(s.quality_score, 0.0)
        s.unified_score = round(
            quality_factor * net_weekly * persistence * vol_penalty * stability, 2
        )

    return signals


def _calc_basis(fr: FundingRate, spread: SpreadData, spot_price: float = 0.0) -> float:
    """Return basis as percentage.
    If spot_price provided: (perp_mark - spot) / spot * 100 (real cross-leg basis).
    Otherwise: (mark - index) / index * 100 (perp internal basis).
    """
    if spot_price > 0 and fr.mark_price > 0:
        return (fr.mark_price - spot_price) / spot_price * 100
    if not (fr.mark_price and fr.index_price and fr.index_price > 0):
        return 0.0
    return (fr.mark_price - fr.index_price) / fr.index_price * 100
