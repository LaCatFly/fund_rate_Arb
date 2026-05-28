"""Tests for database operations."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fund_rate_arb.db import (
    init_db,
    insert_funding_rates,
    query_all_latest,
    query_funding_history,
)


class TestDatabase:
    def _get_tmp_db(self) -> str:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        return path

    def test_init_creates_tables(self):
        db = self._get_tmp_db()
        try:
            init_db(db)
            conn = sqlite3.connect(db)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = {t[0] for t in tables}
            assert "funding_rates" in table_names
            assert "oi_snapshots" in table_names
            assert "spread_data" in table_names
        finally:
            conn.close()

    def test_insert_and_query_funding_rates(self):
        db = self._get_tmp_db()
        try:
            init_db(db)
            rows = [
                (
                    "BTCUSDT",
                    "binance",
                    "2025-01-01T00:00:00",
                    0.0001,
                    0.00012,
                    50000.0,
                    49999.5,
                ),
                (
                    "ETHUSDT",
                    "binance",
                    "2025-01-01T00:00:00",
                    0.00015,
                    0.00015,
                    3000.0,
                    2999.5,
                ),
            ]
            insert_funding_rates(db, rows)

            results = query_all_latest(db, exchange="binance")
            assert len(results) == 2

            symbols = {r["symbol"] for r in results}
            assert "BTCUSDT" in symbols
            assert "ETHUSDT" in symbols
        finally:
            Path(db).unlink()

    def test_duplicate_insert_ignored(self):
        db = self._get_tmp_db()
        try:
            init_db(db)
            rows = [
                (
                    "BTCUSDT",
                    "binance",
                    "2025-01-01T00:00:00",
                    0.0001,
                    0.00012,
                    50000.0,
                    49999.5,
                ),
            ]
            insert_funding_rates(db, rows)
            insert_funding_rates(db, rows)  # duplicate

            results = query_all_latest(db, exchange="binance")
            assert len(results) == 1
        finally:
            Path(db).unlink()

    def test_query_funding_history(self):
        db = self._get_tmp_db()
        try:
            init_db(db)
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")
            t2 = (now - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%S")
            t3 = (now - timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S")
            rows = [
                ("BTCUSDT", "binance", t1, 0.0001, None, 50000.0, None),
                ("BTCUSDT", "binance", t2, 0.00012, None, 50100.0, None),
                ("BTCUSDT", "binance", t3, 0.00011, None, 50050.0, None),
            ]
            insert_funding_rates(db, rows)

            history = query_funding_history(db, "BTCUSDT", "binance", days=1)
            assert len(history) == 3
        finally:
            Path(db).unlink()
