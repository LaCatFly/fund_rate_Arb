"""SQLite database setup and operations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_rates (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    predicted_rate REAL,
    mark_price REAL,
    index_price REAL,
    UNIQUE(symbol, exchange, timestamp)
);

CREATE TABLE IF NOT EXISTS oi_snapshots (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open_interest REAL NOT NULL,
    UNIQUE(symbol, exchange, timestamp)
);

CREATE TABLE IF NOT EXISTS spread_data (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    spread_bps REAL NOT NULL,
    UNIQUE(symbol, exchange, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol
    ON funding_rates(symbol, exchange, timestamp);

CREATE INDEX IF NOT EXISTS idx_oi_snapshots_symbol
    ON oi_snapshots(symbol, exchange, timestamp);

CREATE INDEX IF NOT EXISTS idx_spread_data_symbol
    ON spread_data(symbol, exchange, timestamp);
"""


def get_connection(db_path: str = "fund_rate_arb.db") -> sqlite3.Connection:
    """Get SQLite connection with WAL mode and foreign keys."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str = "fund_rate_arb.db") -> None:
    """Create tables if they don't exist."""
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def insert_funding_rates(
    db_path: str,
    rows: list[tuple[str, str, str, float, float | None, float | None, float | None]],
) -> int:
    """Batch insert funding rates. Returns inserted count."""
    conn = get_connection(db_path)
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO funding_rates
               (symbol, exchange, timestamp, funding_rate, predicted_rate, mark_price, index_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def insert_oi_snapshots(
    db_path: str,
    rows: list[tuple[str, str, str, float]],
) -> int:
    """Batch insert OI snapshots. Returns inserted count."""
    conn = get_connection(db_path)
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO oi_snapshots
               (symbol, exchange, timestamp, open_interest)
               VALUES (?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def insert_spread_data(
    db_path: str,
    rows: list[tuple[str, str, str, float, float, float]],
) -> int:
    """Batch insert spread data. Returns inserted count."""
    conn = get_connection(db_path)
    try:
        conn.executemany(
            """INSERT OR IGNORE INTO spread_data
               (symbol, exchange, timestamp, bid, ask, spread_bps)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def query_funding_history(
    db_path: str,
    symbol: str,
    exchange: str,
    days: int = 30,
) -> list[dict]:
    """Get funding rate history for a symbol."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """SELECT * FROM funding_rates
               WHERE symbol = ? AND exchange = ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (symbol, exchange, days * 4),  # 4 readings per day max (some exchanges do 1h)
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_all_latest(
    db_path: str,
    exchange: str | None = None,
) -> list[dict]:
    """Get latest funding rate per symbol."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if exchange:
            cur = conn.execute(
                """SELECT f.* FROM funding_rates f
                   INNER JOIN (
                       SELECT symbol, MAX(timestamp) as max_ts
                       FROM funding_rates WHERE exchange = ?
                       GROUP BY symbol
                   ) latest ON f.symbol = latest.symbol AND f.timestamp = latest.max_ts
                   WHERE f.exchange = ?""",
                (exchange, exchange),
            )
        else:
            cur = conn.execute(
                """SELECT f.* FROM funding_rates f
                   INNER JOIN (
                       SELECT symbol, exchange, MAX(timestamp) as max_ts
                       FROM funding_rates GROUP BY symbol, exchange
                   ) latest ON f.symbol = latest.symbol
                       AND f.exchange = latest.exchange
                       AND f.timestamp = latest.max_ts"""
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
