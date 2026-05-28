"""Test that scan loop runs strategy tick."""

import pytest
from fund_rate_arb.strategies.funding_carry import FundingCarry
from fund_rate_arb.execution.paper import PaperExecutor
from fund_rate_arb.risk.exit_engine import ExitRuleEngine, TimeBasedRule


@pytest.mark.asyncio
async def test_strategy_tick_returns_result(tmp_path):
    db = str(tmp_path / "test.db")
    from fund_rate_arb.db import init_db, migrate_db
    init_db(db)
    migrate_db(db)

    strategy = FundingCarry(
        executor=PaperExecutor(notional_per_leg=200.0),
        exit_engine=ExitRuleEngine([TimeBasedRule(max_hold_hours=168)]),
        max_positions=3,
        min_apy=15.0,
    )
    result = await strategy.tick(db)
    assert result is not None
    assert isinstance(result.errors, list)
