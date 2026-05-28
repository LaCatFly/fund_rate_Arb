"""Live executor — real orders via collector (classic or PM)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fund_rate_arb.models.funding import CarryPosition
from fund_rate_arb.signal.detector import Signal

logger = logging.getLogger(__name__)


class LiveExecutor:
    """Real order fills via collector."""

    def __init__(self, collector, notional_per_leg: float = 200.0):
        self.collector = collector
        self.notional_per_leg = notional_per_leg

    def open_position(
        self, signal: Signal, execution_id: str | None = None, mark_price: float = 0.0,
    ) -> CarryPosition | None:
        if mark_price <= 0:
            return None

        contracts = self.notional_per_leg / mark_price
        symbol = signal.symbol + "USDT"

        result = self.collector.place_order(
            symbol=symbol, side="sell", amount=contracts,
            order_type="market", position_side="SHORT",
        )
        if result.status not in ("closed", "open"):
            logger.error("Order failed: %s", result.status)
            return None

        return CarryPosition(
            execution_id=execution_id or str(uuid.uuid4()),
            strategy_name="funding_carry", symbol=symbol,
            exchange=self.collector.exchange_name, side="SHORT",
            contracts=round(contracts, 4),
            entry_price=result.average or mark_price,
            entry_basis=0.0, entry_cost=0.0, cumulative_funding=0.0,
            notional_usdt=self.notional_per_leg,
            opened_at=datetime.now(timezone.utc).isoformat(),
            max_break_even_days=10, status="Open",
        )

    def close_position(self, position: CarryPosition, reason: str) -> bool:
        result = self.collector.close_position(
            symbol=position.symbol, amount=position.contracts,
            position_side=position.side.upper(),
        )
        if result.status in ("closed", "open"):
            position.status = "Closed"
            position.close_reason = reason
            logger.info("Closed %s: order %s (%s)",
                        position.symbol, result.order_id, reason)
            return True
        logger.error("Close failed for %s: %s", position.symbol, result.status)
        return False
