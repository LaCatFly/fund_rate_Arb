"""Configuration and constants.

Loads operational parameters from settings.yaml (repo root or ~/.config/fund_rate_arb/).
Falls back to built-in defaults if no file exists.

Public API unchanged: WHITELIST_BINANCE, WHITELIST_HYPERLIQUID, Config, DEFAULT_WEIGHTS, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


# ---------------------------------------------------------------------------
# Built-in defaults (used when settings.yaml is absent)
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "underlyings": [],
    "weights": {
        "funding_mean": 0.30,
        "persistence": 0.25,
        "volatility": -0.15,
        "oi_stability": 0.15,
        "spread_cost": -0.10,
        "slippage": -0.05,
    },
    "fees": {
        "binance_maker": 0.0002,
        "binance_taker": 0.0005,
        "hyperliquid_maker": 0.0000,
        "hyperliquid_taker": 0.00035,
        "slippage": 0.0003,
        "spread_cost": 0.0002,
    },
    "strategy": {
        "funding_intervals_per_day": 3,
        "funding_lookback_hours": 24,
        "min_oi_usd": 10_000_000,
        "top_n_symbols": 50,
        "min_apy_threshold": 10.0,
        "max_concurrent_positions": 5,
        "min_notional": 5.50,
        "default_leverage": 1,
    },
    "network": {
        "binance_proxy": "http://127.0.0.1:7897",
        "binance_futures_base": "https://fapi.binance.com",
        "hyperliquid_api": "https://api.hyperliquid.xyz",
        "hip3_prefix": "xyz:",
    },
    "strategies": [],
}


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------

def _resolve_settings_path() -> Path | None:
    """Check repo root then user config dir for settings.yaml."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "settings.yaml",
        Path.home() / ".config" / "fund_rate_arb" / "settings.yaml",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _load_settings() -> dict[str, Any]:
    """Load settings.yaml or return defaults."""
    if not HAS_YAML:
        return dict(DEFAULT_SETTINGS)
    path = _resolve_settings_path()
    if path is None:
        return dict(DEFAULT_SETTINGS)
    with open(path) as f:
        user = yaml.safe_load(f) or {}
    # Deep merge user over defaults
    result = dict(DEFAULT_SETTINGS)
    for section, values in user.items():
        if isinstance(values, dict) and isinstance(result.get(section), dict):
            result[section].update(values)
        else:
            result[section] = values
    return result


_RAW = _load_settings()


# ---------------------------------------------------------------------------
# Underlying model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Underlying:
    ticker: str
    name: str
    binance_f: str | None
    binance_s: str | None
    hl_perp: str | None
    hl_spot: str | None
    sector: str


def _parse_underlyings(raw: list[dict]) -> list[Underlying]:
    """Convert YAML list to Underlying objects."""
    results = []
    for entry in raw:
        results.append(Underlying(
            ticker=entry["ticker"],
            name=entry.get("name", entry["ticker"]),
            binance_f=entry.get("binance_f"),
            binance_s=entry.get("binance_s"),
            hl_perp=entry.get("hl_perp"),
            hl_spot=entry.get("hl_spot"),
            sector=entry.get("sector", "equity"),
        ))
    return results


UNDERLYINGS: list[Underlying] = _parse_underlyings(_RAW.get("underlyings", []))
_UNDERLYING_BY_TICKER = {u.ticker: u for u in UNDERLYINGS}


# ---------------------------------------------------------------------------
# Per-exchange whitelists — derived from UNDERLYINGS
# ---------------------------------------------------------------------------

WHITELIST_BINANCE: set[str] = {
    u.binance_f for u in UNDERLYINGS if u.binance_f is not None and u.sector != "crypto"
}

WHITELIST_BINANCE_SPOT: set[str] = {
    u.binance_s for u in UNDERLYINGS if u.binance_s is not None and u.sector != "crypto"
}

WHITELIST_HYPERLIQUID: set[str] = {
    u.hl_perp for u in UNDERLYINGS if u.hl_perp is not None and u.sector != "crypto"
}

WHITELIST_HYPERLIQUID_SPOT: set[str] = {
    u.hl_spot for u in UNDERLYINGS if u.hl_spot is not None and u.sector != "crypto"
}

WHITELIST_SYMBOLS = sorted({u.ticker for u in UNDERLYINGS if u.sector != "crypto"})

# Module-level network constants (for collectors that import them directly)
BINANCE_FUTURES_BASE: str = _RAW["network"]["binance_futures_base"]
BINANCE_SPOT_BASE: str = "https://api.binance.com"
HYPERLIQUID_API: str = _RAW["network"]["hyperliquid_api"]
HIP3_PREFIX: str = _RAW["network"]["hip3_prefix"]
FUNDING_LOOKBACK_HOURS: int = _RAW["strategy"]["funding_lookback_hours"]

# Scoring weights and fees
DEFAULT_WEIGHTS: dict[str, float] = dict(_RAW["weights"])
DEFAULT_FEES: dict[str, float] = dict(_RAW["fees"])


def get_strategy_specs(settings: dict | None = None) -> list:
    """Load strategy specs from settings dict (or load from YAML)."""
    from fund_rate_arb.strategies.config import parse_strategy_specs

    if settings is None:
        settings = _load_settings()
    raw = settings.get("strategies", [])
    return parse_strategy_specs(raw)


# Strategy constants
FUNDING_INTERVALS_PER_DAY: int = _RAW["strategy"]["funding_intervals_per_day"]
FUNDING_INTERVALS_PER_YEAR: int = FUNDING_INTERVALS_PER_DAY * 365
BINANCE_PROXY: str = os.environ.get("BINANCE_PROXY", _RAW["network"]["binance_proxy"])


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def get_underlying(ticker: str) -> Underlying | None:
    """Return canonical underlying definition for a ticker."""
    return _UNDERLYING_BY_TICKER.get(ticker)


def binance_to_underlying(symbol: str) -> Underlying | None:
    """Resolve Binance Futures symbol (e.g. TSLAUSDT) to Underlying."""
    for u in UNDERLYINGS:
        if u.binance_f == symbol:
            return u
    return None


def hl_to_underlying(ticker: str) -> Underlying | None:
    """Resolve Hyperliquid perp name (no xyz: prefix) to Underlying."""
    for u in UNDERLYINGS:
        if u.hl_perp == ticker:
            return u
    return None


def cross_exchange_pairs() -> list[tuple[Underlying, str, str]]:
    """Return (underlying, binance_symbol, hl_symbol) for assets on both venues."""
    pairs = []
    for u in UNDERLYINGS:
        if u.binance_f and u.hl_perp:
            pairs.append((u, u.binance_f, u.hl_perp))
    return pairs


# ---------------------------------------------------------------------------
# Runtime Config dataclass (used by scoring)
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Runtime configuration."""
    db_path: str = "fund_rate_arb.db"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    fees: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FEES))
    top_n_symbols: int = _RAW["strategy"]["top_n_symbols"]
    min_oi_usd: float = _RAW["strategy"]["min_oi_usd"]
    min_apy_threshold: float = _RAW["strategy"]["min_apy_threshold"]
    max_concurrent_positions: int = _RAW["strategy"]["max_concurrent_positions"]
    min_notional: float = _RAW["strategy"]["min_notional"]
    default_leverage: int = _RAW["strategy"]["default_leverage"]
