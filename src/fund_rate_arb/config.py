"""Configuration and constants."""

from __future__ import annotations

from dataclasses import dataclass, field
import os

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz"

# Whitelisted tickers for TradeFI/US stock scans only
# Hyperliquid: HIP-3 equity perps use "xyz:" namespace prefix in native API.
# Symbol format for cross-exchange display: TSLA (no suffix)
WHITELIST_HYPERLIQUID = {
    # Top equity perps
    "TSLA", "NVDA", "AAPL", "MSFT", "AMZN",
    "META", "GOOGL", "PLTR", "MSTR", "COIN",
    "AMD", "HOOD", "ORCL", "INTC", "NFLX",
    # Commodities/ETFs on Hyperliquid
    "BABA", "MU", "CRCL",
    # Indices
    "SPCX", "SP500", "XYZ100",
}

# Binance: stock-like USDT perpetuals on fapi.binance.com
WHITELIST_BINANCE = {
    "TSLAUSDT", "NVDAUSDT", "AAPLUSDT", "MSFTUSDT", "AMZNUSDT",
    "METAUSDT", "GOOGLUSDT", "PLTRUSDT", "MSTRUSDT", "COINUSDT",
    "AMDUSDT", "HOODUSDT", "ORCLUSDT", "INTCUSDT",
    "BABAUSDT", "MUUSDT", "CRCLUSDT",
}

# Combined display base names (no suffix)
WHITELIST_SYMBOLS = sorted(
    WHITELIST_HYPERLIQUID | {s.replace("USDT", "") for s in WHITELIST_BINANCE}
)

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

# HTTP proxy for Binance (Clash Meta mixed port). Override via BINANCE_PROXY env var.
# Empty string = direct connection.
BINANCE_PROXY = os.environ.get("BINANCE_PROXY", "http://127.0.0.1:7897")


@dataclass
class Config:
    """Runtime configuration."""
    db_path: str = "fund_rate_arb.db"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    fees: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEES))
    top_n_symbols: int = 50
    min_oi_usd: float = 10_000_000  # $10M minimum open interest
