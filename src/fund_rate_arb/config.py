"""Configuration and constants."""

from __future__ import annotations

from dataclasses import dataclass, field

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
HYPERLIQUID_API = "https://api.hyperliquid.xyz"

# Whitelisted tickers for TradeFI/US stock scans only
# Hyperliquid: HIP-3 equity perps (Trade[XYZ] + Felix), symbol = NAME + USDT
WHITELIST_HYPERLIQUID = {
    # Top equity perps
    "TSLAUSDT", "NVDAUSDT", "AAPLUSDT", "MSFTUSDT", "AMZNUSDT",
    "METAUSDT", "GOOGLUSDT", "PLTRUSDT", "MSTRUSDT", "COINUSDT",
    "AMDUSDT", "HOODUSDT", "ORCLUSDT", "INTCUSDT",
    # Pre-IPO
    "SPCXUSDT", "OPENAIUSDT",
    # Indices
    "SP500USDT", "XYZ100USDT",
}

# Binance: Ondo tokenized stocks on Binance Alpha (spot tokens, not USDT perps)
# WARNING: these are NOT on fapi.binance.com (futures API). Current BinanceCollector
# only hits the Futures API. These symbols will return 0 results until a spot collector
# for Binance Alpha is added (uses Binance spot API or Ondo on-chain data).
WHITELIST_BINANCE = {
    "AAPLon", "TSLAon", "NVDAon", "GOOGLon", "METAon",
    "AMZNon", "MSFTon", "NFLXon", "CRCLon", "QQQon",
    "COINon", "HOODon", "PLTRon", "MUon", "ORCLon",
    "INTCon", "MSTRon", "ABNBon", "JDon", "BABAon",
    "SLVon", "XYZon", "MTZon",
}

# Combined display names
WHITELIST_SYMBOLS = sorted(WHITELIST_HYPERLIQUID | {s.replace("on", "").upper() for s in WHITELIST_BINANCE})

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
