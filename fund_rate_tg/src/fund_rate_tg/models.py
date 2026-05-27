from dataclasses import dataclass


@dataclass
class Signal:
    exchange: str  # "BN" or "HL"
    symbol: str    # "BTC", "ETH", ...
    apy_net: float       # net annualized %
    apy_gross: float     # gross annualized %
    cost: float          # total cost %
    basis_pct: float     # perp-spot basis %
    spread_bps: float    # bid-ask spread in basis points
    interval_h: int      # funding interval hours (1 or 8)
