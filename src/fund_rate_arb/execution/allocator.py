"""Capital allocation across concurrent positions."""

from __future__ import annotations


class Allocator:
    """Track capital and slot usage for concurrent positions."""

    def __init__(
        self,
        total_capital: float,
        max_concurrent: int,
        notional_per_leg: float,
    ):
        self.total_capital = total_capital
        self.max_concurrent = max_concurrent
        self.notional_per_leg = notional_per_leg
        self._used_slots: int = 0

    @property
    def available_slots(self) -> int:
        return self.max_concurrent - self._used_slots

    @property
    def available_capital(self) -> float:
        return self.total_capital - self._used_slots * self.notional_per_leg

    def can_allocate(self) -> bool:
        return (
            self.available_slots > 0 and self.available_capital >= self.notional_per_leg
        )

    def allocate(self) -> bool:
        if not self.can_allocate():
            return False
        self._used_slots += 1
        return True

    def release(self) -> None:
        if self._used_slots > 0:
            self._used_slots -= 1
