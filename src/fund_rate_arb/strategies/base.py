"""Strategy abstract base class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from fund_rate_arb.models.funding import CarryPosition, ExitSignal, MarketData
from fund_rate_arb.signal.detector import Signal


@dataclass
class StrategyResult:
    """Outcome of one strategy tick."""

    positions_opened: int = 0
    positions_closed: int = 0
    signals_generated: int = 0
    errors: list[str] = field(default_factory=list)


class BaseStrategy(ABC):
    """Strategy lifecycle interface."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def tick(self, db_path: str) -> StrategyResult:
        """Run one full cycle: select, execute, monitor, exit."""

    @abstractmethod
    def select(self, signals: list[Signal], open_positions: list[CarryPosition]) -> list[Signal]:
        """Filter signals into candidates for new positions."""

    @abstractmethod
    async def open_position(self, signal: Signal, db_path: str) -> CarryPosition | None:
        """Open a new position from a signal."""

    @abstractmethod
    async def monitor_position(self, position: CarryPosition, db_path: str) -> list[ExitSignal]:
        """Check exit conditions for a position."""

    @abstractmethod
    async def exit_position(self, position: CarryPosition, reason: str, db_path: str) -> bool:
        """Close a position."""
