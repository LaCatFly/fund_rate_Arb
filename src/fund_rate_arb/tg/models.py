from dataclasses import dataclass


@dataclass
class Signal:
    exchange: str  # "BN" or "HL"
    symbol: str    # "TSM", "NVDA", ...
    apy_net: float       # net annualized %
    apy_gross: float     # gross annualized %
    cost: float          # total cost %
    basis_pct: float     # perp-spot basis %
    spread_bps: float    # bid-ask spread in basis points
    interval_h: int      # funding interval hours (1 or 8)
    # 72h history
    avg_rate_72h: float = 0.0
    std_rate_72h: float = 0.0
    positive_ratio_72h: float = 0.0
    # Ranking
    score_daily: float = 0.0
    score_weekly: float = 0.0
