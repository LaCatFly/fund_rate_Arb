"""Tests for Phase 1: Data foundation."""

from __future__ import annotations

import json
import sqlite3

import pytest

from fund_rate_arb.db import (
    init_db,
    migrate_db,
    insert_funding_rates,
    insert_oi_snapshots,
    insert_strategy_position,
    update_position_funding,
    close_strategy_position,
    insert_trade_log,
    query_trade_log,
    query_open_positions_by_strategy,
    query_last_close_time,
    query_oi_history,
    query_funding_range,
)
from fund_rate_arb.events.bus import EventBus
from fund_rate_arb.data.monitors import (
    MonitorResult,
    detect_oi_spike,
    detect_funding_regime_shift,
    compute_funding_zscore,
    compute_ewma,
    compute_basis_drift,
)
from fund_rate_arb.data.retriever import (
    query_funding_window,
    query_oi_window,
    query_latest_basis,
    query_cumulative_funding,
)
from fund_rate_arb.data.payments import (
    record_funding_payment,
    query_position_funding_summary,
)
from fund_rate_arb.models.funding import (
    CarryPosition,
    ExitSignal,
    MarketData,
    FundingSummary,
)


@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with schema + migration applied."""
    path = str(tmp_path / "test.db")
    init_db(path)
    migrate_db(path)
    return path


# --- DB Tests ---


class TestMigration:
    def test_migration_idempotent(self, db_path):
        """Running migrate_db twice should not fail."""
        migrate_db(db_path)
        migrate_db(db_path)

    def test_positions_has_new_columns(self, db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.execute("PRAGMA table_info(positions)")
        columns = {row[1] for row in cur.fetchall()}
        conn.close()
        assert "strategy_name" in columns
        assert "entry_basis" in columns
        assert "cumulative_funding" in columns
        assert "execution_id" in columns

    def test_trade_log_table_exists(self, db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='trade_log'"
        )
        assert cur.fetchone() is not None
        conn.close()


class TestStrategyPosition:
    def test_insert_returns_execution_id(self, db_path):
        row = (
            "TSLA", "binance", "SHORT", 10.0, 100.0, 100.0,
            0.0, 500.0, 1, "2024-01-01T00:00:00", "open",
            "funding_carry", 0.001, 0.0, 10,
        )
        exec_id = insert_strategy_position(db_path, row)
        assert exec_id is not None
        assert len(exec_id) > 0

    def test_close_strategy_position(self, db_path):
        row = (
            "TSLA", "binance", "SHORT", 10.0, 100.0, 100.0,
            0.0, 500.0, 1, "2024-01-01T00:00:00", "open",
            "funding_carry", 0.001, 0.0, 10,
        )
        exec_id = insert_strategy_position(db_path, row)
        close_strategy_position(db_path, exec_id, "funding_collapse")
        positions = query_open_positions_by_strategy(db_path, "funding_carry")
        assert len(positions) == 0

    def test_query_open_positions_by_strategy(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, side, contracts, entry_price, current_price,
                unrealized_pnl, margin_used, leverage, opened_at, status,
                strategy_name, execution_id)
               VALUES ('TSLA', 'binance', 'SHORT', 10, 100, 100, 0, 500, 1,
                       '2024-01-01', 'open', 'funding_carry', 'test-id-1')"""
        )
        conn.commit()
        conn.close()
        positions = query_open_positions_by_strategy(db_path, "funding_carry")
        assert len(positions) == 1
        assert positions[0]["symbol"] == "TSLA"

    def test_update_position_funding(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, side, contracts, entry_price, current_price,
                unrealized_pnl, margin_used, leverage, opened_at, status,
                execution_id)
               VALUES ('TSLA', 'binance', 'SHORT', 10, 100, 100, 0, 500, 1,
                       '2024-01-01', 'open', 'test-id-2')"""
        )
        conn.commit()
        conn.close()
        update_position_funding(db_path, "test-id-2", 15.5)
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT cumulative_funding FROM positions WHERE execution_id = 'test-id-2'"
        )
        assert cur.fetchone()[0] == 15.5
        conn.close()

    def test_query_last_close_time(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, side, contracts, entry_price, current_price,
                unrealized_pnl, margin_used, leverage, opened_at, updated_at,
                status, strategy_name)
               VALUES ('TSLA', 'binance', 'SHORT', 10, 100, 100, 0, 500, 1,
                       '2024-01-01', '2024-01-02', 'closed', 'funding_carry')"""
        )
        conn.commit()
        conn.close()
        result = query_last_close_time(db_path, "TSLA", "funding_carry")
        assert result == "2024-01-02"


class TestTradeLog:
    def test_insert_and_query(self, db_path):
        insert_trade_log(db_path, "exec-1", "funding_carry", "TSLA", "open")
        insert_trade_log(db_path, "exec-1", "funding_carry", "TSLA", "funding")
        logs = query_trade_log(db_path, "exec-1")
        assert len(logs) == 2
        assert logs[0]["event"] == "open"
        assert logs[1]["event"] == "funding"

    def test_query_empty(self, db_path):
        logs = query_trade_log(db_path, "no-such-id")
        assert logs == []


# --- Events Tests ---


class TestEventBus:
    def test_subscribe_publish(self):
        bus = EventBus()
        received = []
        bus.subscribe("TEST", lambda p: received.append(p))
        bus.publish("TEST", {"key": "value"})
        assert len(received) == 1
        assert received[0] == {"key": "value"}

    def test_multiple_handlers(self):
        bus = EventBus()
        results = []
        bus.subscribe("TEST", lambda p: results.append("a"))
        bus.subscribe("TEST", lambda p: results.append("b"))
        bus.publish("TEST", {})
        assert results == ["a", "b"]

    def test_handler_exception_doesnt_break_others(self):
        bus = EventBus()
        results = []
        bus.subscribe("TEST", lambda p: (_ for _ in ()).throw(Exception("boom")))
        bus.subscribe("TEST", lambda p: results.append("ok"))
        bus.publish("TEST", {})
        assert results == ["ok"]

    def test_no_handlers_no_error(self):
        bus = EventBus()
        bus.publish("UNKNOWN", {"data": 1})  # should not raise


# --- Monitors Tests ---


class TestMonitors:
    def test_oi_spike_triggered(self):
        result = detect_oi_spike([100.0, 150.0], threshold_pct=20.0)
        assert result.triggered is True
        assert result.current_value == 50.0

    def test_oi_spike_not_triggered(self):
        result = detect_oi_spike([100.0, 110.0], threshold_pct=20.0)
        assert result.triggered is False

    def test_oi_spike_insufficient_data(self):
        result = detect_oi_spike([100.0], threshold_pct=20.0)
        assert result.triggered is False

    def test_regime_shift_triggered(self):
        baseline = [1.0, 1.1, 0.9, 1.05, 0.95]  # low std
        current = [1.0, 5.0, 1.0, 5.0, 1.0]  # high std
        result = detect_funding_regime_shift(current, baseline, stdev_multiplier=2.0)
        assert result.triggered is True

    def test_regime_shift_not_triggered(self):
        baseline = [1.0, 2.0, 1.0, 2.0, 1.0]
        current = [1.0, 1.5, 1.0, 1.5, 1.0]
        result = detect_funding_regime_shift(current, baseline, stdev_multiplier=2.0)
        assert result.triggered is False

    def test_zscore(self):
        window = [1.0, 1.0, 1.0, 1.0, 10.0]
        z = compute_funding_zscore(10.0, window)
        assert z > 1.0  # 10 is above mean

    def test_zscore_empty_window(self):
        assert compute_funding_zscore(5.0, []) == 0.0

    def test_ewma(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = compute_ewma(values, span=3)
        assert 3.0 < result < 5.0  # should be closer to recent values

    def test_ewma_empty(self):
        assert compute_ewma([], span=3) == 0.0

    def test_basis_drift(self):
        drift = compute_basis_drift(101.0, 100.0, 0.005)  # current 1%, entry 0.5%
        assert abs(drift - 0.005) < 0.001


# --- Retriever Tests ---


class TestRetriever:
    def test_query_funding_window(self, db_path):
        # Insert rows with timestamps relative to a fixed point
        insert_funding_rates(db_path, [
            ("TSLA", "binance", "2024-01-01T00:00:00", 0.001, None, None, None),
            ("TSLA", "binance", "2024-01-01T08:00:00", 0.002, None, None, None),
        ])
        # Use query_funding_range directly to avoid NOW() dependency
        rates = query_funding_range(db_path, "TSLA", "binance", "2024-01-01T00:00:00", "2024-12-31T00:00:00")
        assert len(rates) == 2

    def test_query_funding_window_empty(self, db_path):
        rates = query_funding_window(db_path, "FAKE", "binance", hours=24)
        assert rates == []

    def test_query_oi_window(self, db_path):
        insert_oi_snapshots(db_path, [
            ("TSLA", "binance", "2024-01-01T00:00:00", 1000.0),
            ("TSLA", "binance", "2024-01-01T08:00:00", 1500.0),
        ])
        oi = query_oi_window(db_path, "TSLA", "binance", hours=87600)
        assert len(oi) == 2
        assert oi == [1000.0, 1500.0]

    def test_query_latest_basis(self, db_path):
        insert_funding_rates(db_path, [
            ("TSLA", "binance", "2024-01-01T00:00:00", 0.001, None, 101.0, 100.0),
        ])
        basis = query_latest_basis(db_path, "TSLA", "binance")
        assert basis is not None
        assert abs(basis - 0.01) < 0.001  # (101-100)/100 = 0.01

    def test_query_latest_basis_no_data(self, db_path):
        basis = query_latest_basis(db_path, "FAKE", "binance")
        assert basis is None

    def test_query_cumulative_funding(self, db_path):
        # Insert directly with distinct timestamps to avoid UNIQUE collision
        from fund_rate_arb.db import get_connection
        conn = get_connection(db_path)
        conn.execute(
            """INSERT INTO trade_log (execution_id, strategy_name, symbol, event, timestamp, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("exec-1", "funding_carry", "TSLA", "funding", "2024-01-01T08:00:00",
             json.dumps({"rate": 0.001, "amount": 0.5})),
        )
        conn.execute(
            """INSERT INTO trade_log (execution_id, strategy_name, symbol, event, timestamp, details)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("exec-1", "funding_carry", "TSLA", "funding", "2024-01-01T16:00:00",
             json.dumps({"rate": 0.002, "amount": 1.0})),
        )
        conn.commit()
        conn.close()
        cumulative = query_cumulative_funding(db_path, "exec-1")
        assert abs(cumulative - 1.5) < 0.001

    def test_query_cumulative_funding_empty(self, db_path):
        cumulative = query_cumulative_funding(db_path, "no-such-id")
        assert cumulative == 0.0


# --- Payments Tests ---


class TestPayments:
    def test_record_and_query(self, db_path):
        record_funding_payment(db_path, "exec-1", "TSLA", 0.001, 0.5, "2024-01-01T08:00:00")
        record_funding_payment(db_path, "exec-1", "TSLA", 0.002, 1.0, "2024-01-01T16:00:00")
        summary = query_position_funding_summary(db_path, "exec-1")
        assert summary.count == 2
        assert abs(summary.total_payments - 1.5) < 0.001
        assert summary.last_payment_ts == "2024-01-01T16:00:00"

    def test_empty_summary(self, db_path):
        summary = query_position_funding_summary(db_path, "no-such-id")
        assert summary.total_payments == 0.0
        assert summary.count == 0


# --- Models Tests ---


class TestModels:
    def test_carry_position_defaults(self):
        pos = CarryPosition(
            execution_id="test", strategy_name="funding_carry", symbol="TSLA",
            exchange="binance_pm", side="SHORT", contracts=10.0, entry_price=100.0,
            entry_basis=0.001, entry_cost=0.5, cumulative_funding=0.0,
            notional_usdt=1000.0, opened_at="2024-01-01", max_break_even_days=10,
            status="Open",
        )
        assert pos.close_reason is None

    def test_exit_signal(self):
        sig = ExitSignal("exec-1", "funding_collapse", "critical", "test")
        assert sig.severity == "critical"

    def test_market_data_defaults(self):
        md = MarketData(
            symbol="TSLA", exchange="binance", current_mark=101.0,
            current_index=100.0, current_basis=0.01,
            funding_history_48h=[0.001], oi_window_8h=[1000.0],
        )
        assert md.distance_to_liq_pct is None
        assert md.predicted_funding is None
