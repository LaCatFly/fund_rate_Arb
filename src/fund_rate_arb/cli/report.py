"""Generate funding rate report — positive funding, APY > threshold."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

console = Console()


def generate_report(
    db_path: str = "fund_rate_arb.db",
    min_apy: float = 10.0,
    output: str | None = None,
) -> list[dict]:
    """Generate report of symbols with positive funding above min_apy."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute('''
        SELECT f.* FROM funding_rates f
        INNER JOIN (
            SELECT symbol, exchange, MAX(timestamp) as max_ts
            FROM funding_rates GROUP BY symbol, exchange
        ) latest ON f.symbol = latest.symbol
            AND f.exchange = latest.exchange
            AND f.timestamp = latest.max_ts
        ORDER BY f.funding_rate DESC
    ''').fetchall()

    oi_rows = conn.execute('SELECT * FROM oi_snapshots').fetchall()
    spread_rows = conn.execute('SELECT * FROM spread_data').fetchall()
    conn.close()

    oi_map = {(r['symbol'], r['exchange']): r['open_interest'] for r in oi_rows}
    spread_map = {(r['symbol'], r['exchange']): r['spread_bps'] for r in spread_rows}
    mark_map = {(r['symbol'], r['exchange']): r['mark_price'] for r in spread_rows
                if r['mark_price']}

    results = []
    for r in rows:
        fr = r['funding_rate']
        # Hyperliquid: hourly rates → * 8760 (24h * 365)
        # Binance: per-8h rates → * 1095 (3 * 365)
        multiplier = 8760 if r['exchange'] == 'hyperliquid' else 1095
        apy = fr * multiplier * 100
        if apy < min_apy:
            continue

        sym = r['symbol']
        ex = r['exchange']
        oi = oi_map.get((sym, ex), None)
        sp = spread_map.get((sym, ex), None)
        mk = mark_map.get((sym, ex), None) or r['mark_price']

        results.append({
            'symbol': sym,
            'exchange': ex,
            'funding_rate': fr,
            'apy': apy,
            'oi': float(oi) if oi else None,
            'spread_bps': float(sp) if sp else None,
            'mark_price': mk,
        })

    return results


def display_table(results: list[dict]) -> None:
    """Display results as a rich table."""
    table = Table(title=f"Candidates: APY > 10% ({len(results)} found)")
    table.add_column("#")
    table.add_column("Symbol")
    table.add_column("Exchange")
    table.add_column("Funding/8h", justify="right")
    table.add_column("APY%", justify="right")
    table.add_column("OI", justify="right")
    table.add_column("Spread(bps)", justify="right")
    table.add_column("Mark", justify="right")

    for i, r in enumerate(results, 1):
        oi_str = f"{r['oi']:,.0f}" if r['oi'] else "N/A"
        sp_str = f"{r['spread_bps']:.2f}" if r['spread_bps'] else "N/A"
        mark = f"{r['mark_price']:.2f}" if r['mark_price'] else "N/A"
        table.add_row(
            str(i), r['symbol'], r['exchange'],
            f"{r['funding_rate']*100:.5f}%",
            f"{r['apy']:.1f}%",
            oi_str, sp_str, mark,
        )

    console.print(table)


def save_report(results: list[dict], path: str, compact: bool = False) -> None:
    """Save report in Telegram-friendly format (no markdown tables)."""
    ts = datetime.now(timezone.utc)
    timestamp = ts.strftime('%Y-%m-%d %H:%M UTC')
    time_short = ts.strftime('%H:%M UTC')

    if compact:
        exchange_short = {"binance": "BN", "hyperliquid": "HL"}
        lines = [
            f"📊 Funding — {time_short} | APY > 10%",
            "",
        ]
        for i, r in enumerate(results, 1):
            ex = exchange_short.get(r['exchange'], r['exchange'])
            sp = f" {r['spread_bps']:.1f}bps" if r['spread_bps'] else ""
            oi = f" OI {r['oi']/1000:.0f}K" if r['oi'] else ""
            mark = f" ${r['mark_price']:.0f}" if r['mark_price'] else ""
            lines.append(
                f"**{r['symbol']}** @ {ex} — **{r['apy']:.1f}%** | "
                f"{r['funding_rate']*100:.4f}%{oi}{sp}{mark}"
            )
        lines.extend(["", f"_{len(results)} candidates_"])
    else:
        lines = [
            f"📊 Funding Rate Report — {timestamp}",
            "",
            "Criteria: Positive funding, APY > 10%",
            "",
        ]
        for i, r in enumerate(results, 1):
            oi_str = f"{r['oi']:,.0f}" if r['oi'] else "N/A"
            sp_str = f"{r['spread_bps']:.2f} bps" if r['spread_bps'] else "N/A"
            mark = f"${r['mark_price']:.2f}" if r['mark_price'] else "N/A"
            funding_pct = f"{r['funding_rate']*100:.5f}%"

            lines.append(
                f"{i}. **{r['symbol']}** @ {r['exchange']} — **{r['apy']:.1f}% APY**"
            )
            lines.append(
                f"   Funding/8h: {funding_pct}  |  OI: {oi_str}  |  "
                f"Spread: {sp_str}  |  Mark: {mark}"
            )
            lines.append("")

        lines.extend([
            f"**Total candidates:** {len(results)}",
            "",
            "———",
            "_Generated by fund-rate-arb. Data: Binance Futures + Hyperliquid REST._",
        ])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n")
    console.print(f"[green]Report saved to {path}[/]")


@click.command()
@click.option("--db", default="fund_rate_arb.db", help="SQLite database path")
@click.option("--min-apy", default=10.0, help="Minimum APY% threshold")
@click.option("--output", "-o", default="reports/funding-report-latest.md", help="Output path")
@click.option("--compact/--full", default=False, help="Compact single-line format")
def cli(db: str, min_apy: float, output: str, compact: bool) -> None:
    """Generate funding rate report for symbols with positive funding above threshold."""
    results = generate_report(db, min_apy, output)
    if not results:
        console.print(f"[yellow]No candidates with APY > {min_apy}%[/]")
        return
    display_table(results)
    save_report(results, output, compact=compact)


if __name__ == "__main__":
    cli()
