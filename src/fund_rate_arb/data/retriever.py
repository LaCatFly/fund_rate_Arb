"""Time-series query functions for position monitoring."""

from __future__ import annotations

from fund_rate_arb.db import (
    get_connection,
    query_funding_range,
    query_oi_history,
)


def query_funding_window(db_path: str, symbol: str, exchange: str, hours: int) -> list[float]:
    """Return funding rates in the last N hours, oldest first."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT funding_rate FROM funding_rates
               WHERE symbol = ? AND exchange = ?
               AND timestamp >= datetime('now', ?)
               ORDER BY timestamp ASC""",
            (symbol, exchange, f"-{hours} hours"),
        )
        return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def query_oi_window(db_path: str, symbol: str, exchange: str, hours: int) -> list[float]:
    """Return OI snapshots in the last N hours."""
    rows = query_oi_history(db_path, symbol, exchange, hours)
    return [r["open_interest"] for r in rows]


def query_latest_basis(db_path: str, symbol: str, exchange: str = "binance") -> float | None:
    """Return current basis (mark - index) / index from funding_rates table."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT mark_price, index_price FROM funding_rates
               WHERE symbol = ? AND exchange = ?
               AND mark_price IS NOT NULL AND index_price IS NOT NULL
               ORDER BY timestamp DESC LIMIT 1""",
            (symbol, exchange),
        )
        row = cur.fetchone()
        if row and row[1]:
            return (row[0] - row[1]) / row[1]
        return None
    finally:
        conn.close()


def query_cumulative_funding(db_path: str, execution_id: str) -> float:
    """Sum of all funding_payment events for this position."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT COALESCE(SUM(CAST(json_extract(details, '$.amount') AS REAL)), 0)
               FROM trade_log
               WHERE execution_id = ? AND event = 'funding'""",
            (execution_id,),
        )
        row = cur.fetchone()
        return row[0] if row else 0.0
    finally:
        conn.close()
