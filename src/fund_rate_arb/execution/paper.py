"""Paper executor — simulated fills for strategy testing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fund_rate_arb.models.funding import CarryPosition
from fund_rate_arb.signal.detector import Signal


class PaperExecutor:
    """Simulated order fills at mark price. No real orders."""

    def __init__(self, notional_per_leg: float = 200.0):
        self.notional_per_leg = notional_per_leg

    def open_position(
        self,
        signal: Signal,
        execution_id: str | None = None,
        mark_price: float = 0.0,
        side: str = "SHORT",
    ) -> CarryPosition | None:
        """Open simulated position."""
        if mark_price <= 0:
            return None

        contracts = self.notional_per_leg / mark_price
        return CarryPosition(
            execution_id=execution_id or str(uuid.uuid4()),
            strategy_name="funding_carry",
            symbol=signal.symbol + "USDT",
            exchange="paper",
            side=side,
            contracts=round(contracts, 4),
            entry_price=mark_price,
            entry_basis=0.0,
            entry_cost=round(self.notional_per_leg * signal.cost / 100, 2),
            cumulative_funding=0.0,
            notional_usdt=self.notional_per_leg,
            opened_at=datetime.now(timezone.utc).isoformat(),
            max_break_even_days=10,
            status="Open",
        )

    def close_position(self, position: CarryPosition, reason: str) -> bool:
        """Simulate closing position."""
        position.status = "Closed"
        position.close_reason = reason
        return True
