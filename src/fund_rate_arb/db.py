"""SQLite database setup and operations."""

from __future__ import annotations

import sqlite3
import uuid
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

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL,
    contracts REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL DEFAULT 0,
    unrealized_pnl REAL DEFAULT 0,
    margin_used REAL DEFAULT 0,
    leverage INTEGER DEFAULT 1,
    opened_at TEXT NOT NULL,
    updated_at TEXT,
    status TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    side TEXT NOT NULL,
    type TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL,
    filled REAL DEFAULT 0,
    average REAL,
    status TEXT NOT NULL,
    position_side TEXT,
    cost REAL,
    fee REAL,
    pnl REAL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(order_id)
);

CREATE INDEX IF NOT EXISTS idx_positions_symbol
    ON positions(symbol, status);

CREATE INDEX IF NOT EXISTS idx_trades_symbol
    ON trades(symbol, created_at);
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


def migrate_db(db_path: str = "fund_rate_arb.db") -> None:
    """Add strategy columns and trade_log table. Safe to re-run."""
    conn = get_connection(db_path)
    try:
        # ALTER TABLE ADD COLUMN IF NOT EXISTS requires SQLite 3.35.0+;
        # fall back to try/except for older builds.
        alter_stmts = [
            "ALTER TABLE positions ADD COLUMN strategy_name TEXT",
            "ALTER TABLE positions ADD COLUMN entry_basis REAL DEFAULT 0",
            "ALTER TABLE positions ADD COLUMN cumulative_funding REAL DEFAULT 0",
            "ALTER TABLE positions ADD COLUMN max_break_even_days INTEGER DEFAULT 10",
            "ALTER TABLE positions ADD COLUMN close_reason TEXT",
            "ALTER TABLE positions ADD COLUMN execution_id TEXT",
            "ALTER TABLE trades ADD COLUMN strategy_name TEXT",
            "ALTER TABLE trades ADD COLUMN execution_id TEXT",
            "ALTER TABLE trades ADD COLUMN event_type TEXT",
        ]
        for stmt in alter_stmts:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY,
                execution_id TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                details TEXT,
                UNIQUE(execution_id, event, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_trade_log_execution ON trade_log(execution_id);
            CREATE INDEX IF NOT EXISTS idx_trade_log_event ON trade_log(event);
        """)
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
               AND timestamp >= datetime('now', ?)
               ORDER BY timestamp DESC""",
            (symbol, exchange, f"-{days} days"),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_all_latest(
    db_path: str,
    exchange: str | None = None,
) -> list[dict]:
    """Get latest funding rate per symbol, joined with spread and OI."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if exchange:
            cur = conn.execute(
                """SELECT f.*, COALESCE(o.open_interest, 0) as open_interest,
                       COALESCE(s.spread_bps, 0) as spread_bps
                   FROM funding_rates f
                   INNER JOIN (
                       SELECT symbol, MAX(timestamp) as max_ts
                       FROM funding_rates WHERE exchange = ?
                       GROUP BY symbol
                   ) latest ON f.symbol = latest.symbol AND f.timestamp = latest.max_ts
                   LEFT JOIN (
                       SELECT symbol, open_interest FROM oi_snapshots
                       WHERE exchange = ? AND timestamp >= datetime('now', '-1 hours')
                   ) o ON f.symbol = o.symbol
                   LEFT JOIN (
                       SELECT symbol, spread_bps FROM spread_data
                       WHERE exchange = ? AND timestamp >= datetime('now', '-1 hours')
                   ) s ON f.symbol = s.symbol
                   WHERE f.exchange = ?""",
                (exchange, exchange, exchange, exchange),
            )
        else:
            cur = conn.execute(
                """SELECT f.*, COALESCE(o.open_interest, 0) as open_interest,
                       COALESCE(s.spread_bps, 0) as spread_bps
                   FROM funding_rates f
                   INNER JOIN (
                       SELECT symbol, exchange, MAX(timestamp) as max_ts
                       FROM funding_rates GROUP BY symbol, exchange
                   ) latest ON f.symbol = latest.symbol
                       AND f.exchange = latest.exchange
                       AND f.timestamp = latest.max_ts
                   LEFT JOIN (
                       SELECT symbol, exchange, open_interest FROM oi_snapshots
                       WHERE timestamp >= datetime('now', '-1 hours')
                   ) o ON f.symbol = o.symbol AND f.exchange = o.exchange
                   LEFT JOIN (
                       SELECT symbol, exchange, spread_bps FROM spread_data
                       WHERE timestamp >= datetime('now', '-1 hours')
                   ) s ON f.symbol = s.symbol AND f.exchange = s.exchange"""
            )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def insert_position(db_path: str, row: tuple) -> int:
    """Insert a new open position."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, side, contracts, entry_price, current_price,
                unrealized_pnl, margin_used, leverage, opened_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def update_position(db_path: str, symbol: str, side: str, **kwargs) -> None:
    if not kwargs:
        return
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [symbol, side, "open"]
    conn = get_connection(db_path)
    try:
        conn.execute(f"UPDATE positions SET {sets} WHERE symbol = ? AND side = ? AND status = 'open'", values)
        conn.commit()
    finally:
        conn.close()


def close_position(db_path: str, symbol: str, side: str) -> None:
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE positions SET status = 'closed', updated_at = datetime('now') WHERE symbol = ? AND side = ? AND status = 'open'",
            (symbol, side),
        )
        conn.commit()
    finally:
        conn.close()


def query_open_positions(db_path: str) -> list[dict]:
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at DESC")
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def insert_trade(db_path: str, row: tuple) -> int:
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO trades
               (order_id, symbol, exchange, side, type, amount, price, filled, average, status, position_side, cost, fee, pnl, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


def query_recent_trades(db_path: str, limit: int = 20) -> list[dict]:
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_oi_history(db_path: str, symbol: str, exchange: str, hours: int) -> list[dict]:
    """Get OI snapshots in the last N hours."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """SELECT * FROM oi_snapshots
               WHERE symbol = ? AND exchange = ?
               AND timestamp >= datetime('now', ?)
               ORDER BY timestamp ASC""",
            (symbol, exchange, f"-{hours} hours"),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_funding_range(db_path: str, symbol: str, exchange: str, start_ts: str, end_ts: str) -> list[dict]:
    """Get funding rates between two timestamps."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            """SELECT * FROM funding_rates
               WHERE symbol = ? AND exchange = ?
               AND timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp ASC""",
            (symbol, exchange, start_ts, end_ts),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def insert_strategy_position(db_path: str, row: tuple) -> str:
    """Insert a strategy position. Returns execution_id (UUID)."""
    execution_id = str(uuid.uuid4())
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO positions
               (symbol, exchange, side, contracts, entry_price, current_price,
                unrealized_pnl, margin_used, leverage, opened_at, status,
                strategy_name, entry_basis, cumulative_funding, max_break_even_days, execution_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*row, execution_id),
        )
        conn.commit()
        return execution_id
    finally:
        conn.close()


def update_position_funding(db_path: str, execution_id: str, cumulative: float) -> None:
    """Update cumulative funding for a position."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE positions SET cumulative_funding = ? WHERE execution_id = ?",
            (cumulative, execution_id),
        )
        conn.commit()
    finally:
        conn.close()


def close_strategy_position(db_path: str, execution_id: str, reason: str) -> None:
    """Close a strategy position with reason."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """UPDATE positions SET status = 'closed', close_reason = ?, updated_at = datetime('now')
               WHERE execution_id = ?""",
            (reason, execution_id),
        )
        conn.commit()
    finally:
        conn.close()


def insert_trade_log(db_path: str, execution_id: str, strategy: str, symbol: str,
                     event: str, details: str | None = None) -> None:
    """Insert a trade log entry."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO trade_log
               (execution_id, strategy_name, symbol, event, timestamp, details)
               VALUES (?, ?, ?, ?, datetime('now'), ?)""",
            (execution_id, strategy, symbol, event, details),
        )
        conn.commit()
    finally:
        conn.close()


def query_trade_log(db_path: str, execution_id: str) -> list[dict]:
    """Get full trade log for an execution."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM trade_log WHERE execution_id = ? ORDER BY timestamp ASC",
            (execution_id,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_open_positions_by_strategy(db_path: str, strategy_name: str) -> list[dict]:
    """Get open positions for a specific strategy."""
    conn = get_connection(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT * FROM positions WHERE strategy_name = ? AND status = 'open' ORDER BY opened_at DESC",
            (strategy_name,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_last_close_time(db_path: str, symbol: str, strategy_name: str) -> str | None:
    """Get last close timestamp for a symbol+strategy (for reentry cooldown)."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT updated_at FROM positions
               WHERE symbol = ? AND strategy_name = ? AND status = 'closed'
               ORDER BY updated_at DESC LIMIT 1""",
            (symbol, strategy_name),
        )
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()
