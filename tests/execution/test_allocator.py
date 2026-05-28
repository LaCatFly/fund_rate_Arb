"""Tests for capital allocator."""

from fund_rate_arb.execution.allocator import Allocator


class TestAllocator:
    def test_full_capacity_available(self):
        alloc = Allocator(total_capital=1000.0, max_concurrent=5, notional_per_leg=200.0)
        assert alloc.available_slots == 5
        assert alloc.available_capital == 1000.0

    def test_allocate_reduces_slots(self):
        alloc = Allocator(total_capital=1000.0, max_concurrent=5, notional_per_leg=200.0)
        alloc.allocate()
        assert alloc.available_slots == 4
        assert alloc.available_capital == 800.0

    def test_no_slots_when_full(self):
        alloc = Allocator(total_capital=1000.0, max_concurrent=5, notional_per_leg=200.0)
        for _ in range(5):
            alloc.allocate()
        assert alloc.can_allocate() is False

    def test_release_frees_slot(self):
        alloc = Allocator(total_capital=1000.0, max_concurrent=2, notional_per_leg=500.0)
        alloc.allocate()
        alloc.allocate()
        assert alloc.can_allocate() is False
        alloc.release()
        assert alloc.can_allocate() is True
        assert alloc.available_slots == 1

    def test_capacity_based_limit(self):
        """Can't allocate more than capital allows even if slots available."""
        alloc = Allocator(total_capital=300.0, max_concurrent=5, notional_per_leg=200.0)
        alloc.allocate()  # uses 200, 100 left
        assert alloc.can_allocate() is False  # need 200 but only 100 left
