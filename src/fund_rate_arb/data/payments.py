"""Funding payment tracker — track actual payments, not just expected rates."""

from __future__ import annotations

from dataclasses import dataclass

from fund_rate_arb.db import get_connection, insert_trade_log, update_position_funding


@dataclass
class FundingSummary:
    total_payments: float
    count: int
    average_rate: float
    last_payment_ts: str | None


def record_funding_payment(
    db_path: str, execution_id: str, symbol: str,
    rate: float, amount: float, timestamp: str,
) -> None:
    """Record actual funding payment received for a position."""
    import json
    details = json.dumps({"rate": rate, "amount": amount, "timestamp": timestamp})
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO trade_log
               (execution_id, strategy_name, symbol, event, timestamp, details)
               VALUES (?, '', ?, 'funding', ?, ?)""",
            (execution_id, symbol, timestamp, details),
        )
        conn.commit()
    finally:
        conn.close()

    # Update cumulative funding on the position
    summary = query_position_funding_summary(db_path, execution_id)
    update_position_funding(db_path, execution_id, summary.total_payments)


def query_position_funding_summary(db_path: str, execution_id: str) -> FundingSummary:
    """Sum of payments, count, average rate, last payment time."""
    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            """SELECT
                 COALESCE(SUM(CAST(json_extract(details, '$.amount') AS REAL)), 0) as total,
                 COUNT(*) as cnt,
                 MAX(timestamp) as last_ts
               FROM trade_log
               WHERE execution_id = ? AND event = 'funding'""",
            (execution_id,),
        )
        row = cur.fetchone()
        if not row or row[1] == 0:
            return FundingSummary(total_payments=0.0, count=0, average_rate=0.0, last_payment_ts=None)
        return FundingSummary(
            total_payments=row[0],
            count=row[1],
            average_rate=row[0] / row[1],
            last_payment_ts=row[2],
        )
    finally:
        conn.close()
