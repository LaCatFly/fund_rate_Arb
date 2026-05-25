"""Configuration and constants."""

from __future__ import annotations

from dataclasses import dataclass, field

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz"

# Default scoring weights
DEFAULT_WEIGHTS = {
    "funding_mean": 0.30,
    "persistence": 0.25,
    "volatility": -0.15,
    "oi_stability": 0.15,
    "spread_cost": -0.10,
    "slippage": -0.05,
}

# Fee assumptions (maker/taker for retail tier)
DEFAULT_FEES = {
    "binance_maker": 0.0002,    # 0.02%
    "binance_taker": 0.0005,    # 0.05%
    "hyperliquid_maker": 0.0000,  # 0% maker
    "hyperliquid_taker": 0.00035,  # 0.035%
    "slippage": 0.0003,         # 0.03%
    "spread_cost": 0.0002,      # 0.02%
}

FUNDING_INTERVALS_PER_DAY = 3  # every 8 hours
FUNDING_INTERVALS_PER_YEAR = FUNDING_INTERVALS_PER_DAY * 365


@dataclass
class Config:
    """Runtime configuration."""
    db_path: str = "fund_rate_arb.db"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    fees: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEES))
    top_n_symbols: int = 50
    min_oi_usd: float = 10_000_000  # $10M minimum open interest
