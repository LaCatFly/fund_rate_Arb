"""Trading engine: close existing positions for signal testing.

Fetches live LONG positions from PM account, closes them at market price.
Does NOT open new positions — testing only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fund_rate_arb.collectors.portfolio_margin import OrderResult, PortfolioMarginCollector

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Live position from exchange."""
    symbol: str
    side: str
    contracts: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    position_side: str  # LONG or SHORT


class RiskManager:
    """Position sizing and risk limits for PM account."""

    def __init__(
        self,
        max_concurrent_positions: int = 5,
        min_notional: float = 5.50,
    ):
        self.max_concurrent = max_concurrent_positions
        self.min_notional = min_notional

    def check_can_trade(self, collector: PortfolioMarginCollector) -> tuple[bool, str]:
        """Check if PM account can trade."""
        try:
            info = collector.fetch_account_info()
        except Exception as e:
            return False, f"PM account error: {e}"

        if info.available_balance < self.min_notional * 2:
            return False, f"Insufficient balance: {info.available_balance:.2f} USDT"

        if info.account_status != "NORMAL":
            return False, f"Account status: {info.account_status}"

        return True, f"OK: {info.available_balance:.2f} USDT available"


class TradingEngine:
    """Close existing positions at market price for testing."""

    def __init__(
        self,
        collector: PortfolioMarginCollector,
        db_path: str,
        risk_manager: RiskManager | None = None,
    ):
        self.collector = collector
        self.db_path = db_path
        self.risk = risk_manager or RiskManager()

    def fetch_live_positions(self) -> list[Position]:
        """Get all open positions from exchange."""
        raw = self.collector.fetch_positions()
        positions = []
        for p in raw:
            positions.append(Position(
                symbol=p["symbol"],
                side=p["side"],
                contracts=p["contracts"],
                entry_price=p["entry_price"],
                unrealized_pnl=p["unrealized_pnl"],
                leverage=p["leverage"],
                position_side=p.get("position_side", "LONG") if p["contracts"] > 0 else "",
            ))
        return positions

    def close_all_long_positions(self) -> list[OrderResult]:
        """Close all LONG positions at market price. Testing only."""
        can_trade, msg = self.risk.check_can_trade(self.collector)
        if not can_trade:
            logger.warning("Cannot trade: %s", msg)
            return []

        positions = self.fetch_live_positions()
        long_positions = [p for p in positions if p.position_side == "LONG" and p.contracts > 0]

        if not long_positions:
            logger.info("No LONG positions to close")
            return []

        results = []
        for pos in long_positions:
            logger.info("Closing LONG %s: %.4f contracts @ entry $%.2f",
                        pos.symbol, pos.contracts, pos.entry_price)

            result = self.collector.close_position(
                symbol=pos.symbol,
                amount=pos.contracts,
                position_side="LONG",
            )
            results.append(result)
            logger.info("Close %s: status=%s, filled=%.4f, avg=%.2f",
                        result.order_id, result.status, result.filled, result.average or 0)

            try:
                self.record_trade(result)
            except Exception:
                logger.exception("DB record failed for %s", result.order_id)

        return results

    def close_signal_position(self, symbol: str, position_side: str, amount: float) -> OrderResult:
        """Close a specific position at market price."""
        result = self.collector.close_position(
            symbol=symbol,
            amount=amount,
            position_side=position_side,
        )
        logger.info("Close %s %s: status=%s", position_side, symbol, result.status)
        return result

    def record_trade(self, result: OrderResult, exchange: str = "binance_pm") -> None:
        """Persist trade to DB."""
        from fund_rate_arb.db import insert_trade

        cost = (result.average or 0) * result.filled if result.average else 0
        row = (
            result.order_id,
            result.symbol,
            exchange,
            result.side,
            result.type,
            result.amount,
            result.price,
            result.filled,
            result.average,
            result.status,
            result.position_side,
            cost,
            0.0,
            0.0,
            datetime.utcnow().isoformat(),
        )
        insert_trade(self.db_path, row)

    def pm_status(self) -> dict[str, Any]:
        """Get PM account status for CLI display."""
        from fund_rate_arb.db import query_open_positions, query_recent_trades

        account = self.collector.fetch_account_info()
        positions = self.collector.fetch_positions()
        db_positions = query_open_positions(self.db_path)
        recent = query_recent_trades(self.db_path)

        return {
            "account": account,
            "live_positions": positions,
            "db_positions": db_positions,
            "recent_trades": recent,
        }
