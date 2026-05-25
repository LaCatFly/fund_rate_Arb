"""CLI commands for funding rate arbitrage screener."""

from __future__ import annotations

import asyncio
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

from fund_rate_arb.config import Config
from fund_rate_arb.db import (
    init_db,
    insert_funding_rates,
    insert_oi_snapshots,
    insert_spread_data,
    query_all_latest,
    query_funding_history,
)
from fund_rate_arb.collectors import BinanceCollector, HyperliquidCollector
from fund_rate_arb.scoring import compute_quality_score

console = Console()


@click.group()
@click.option("--db", "db_path", default="fund_rate_arb.db", help="SQLite database path")
@click.pass_context
def cli(ctx: click.Context, db_path: str) -> None:
    """Funding rate arbitrage screener for Binance and Hyperliquid."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    init_db(db_path)


@cli.command()
@click.option("--exchange", "-e", type=click.Choice(["binance", "hyperliquid", "all"]), default="all", help="Exchange to fetch")
@click.pass_context
def fetch(ctx: click.Context, exchange: str) -> None:
    """Fetch latest funding rates, OI, and spread data."""
    db_path = ctx.obj["db_path"]

    async def _fetch():
        collectors = []
        if exchange in ("binance", "all"):
            collectors.append(BinanceCollector())
        if exchange in ("hyperliquid", "all"):
            collectors.append(HyperliquidCollector())

        for collector in collectors:
            console.print(f"[blue]Fetching {collector.exchange_name} data...[/]")
            try:
                funding, oi, spreads = await collector.fetch_all()

                if funding:
                    rows = [f.to_db_row() for f in funding]
                    insert_funding_rates(db_path, rows)
                    console.print(f"  [green]✓ {len(funding)} funding rates stored[/]")

                if oi:
                    rows = [o.to_db_row() for o in oi]
                    insert_oi_snapshots(db_path, rows)
                    console.print(f"  [green]✓ {len(oi)} OI snapshots stored[/]")

                if spreads:
                    rows = [s.to_db_row() for s in spreads]
                    insert_spread_data(db_path, rows)
                    console.print(f"  [green]✓ {len(spreads)} spread records stored[/]")

            except Exception as e:
                console.print(f"  [red]✗ {collector.exchange_name} failed: {e}[/]")

    asyncio.run(_fetch())


@cli.command()
@click.option("--top", "-n", default=20, help="Show top N scored symbols")
@click.option("--exchange", "-e", default=None, help="Filter by exchange")
@click.pass_context
def score(ctx: click.Context, top: int, exchange: str | None) -> None:
    """Score and rank funding rate opportunities."""
    db_path = ctx.obj["db_path"]
    config = Config()

    data = query_all_latest(db_path, exchange=exchange)
    if not data:
        console.print("[yellow]No data found. Run 'fetch' first.[/]")
        return

    # Group by symbol to compute scores
    scores = []
    symbol_data: dict[str, list] = {}
    for row in data:
        key = f"{row['symbol']}|{row['exchange']}"
        symbol_data.setdefault(key, []).append(row)

    for key, rows in symbol_data.items():
        symbol, exchange_name = key.split("|")
        history = [r["funding_rate"] for r in rows]

        # Get spread for this symbol
        spread_row = next((r for r in rows if r.get("spread_bps")), None)
        spread_bps = spread_row["spread_bps"] if spread_row else 0.0

        try:
            result = compute_quality_score(
                symbol=symbol,
                exchange=exchange_name,
                funding_history=history,
                spread_bps=spread_bps,
                weights=config.weights,
                fees=config.fees,
            )
            scores.append(result)
        except Exception as e:
            console.print(f"[dim]Error scoring {symbol}: {e}[/]")

    # Sort by score descending
    scores.sort(key=lambda s: s.score, reverse=True)
    top_scores = scores[:top]

    # Display table
    table = Table(title=f"Top {top} Funding Rate Opportunities")
    table.add_column("#", style="dim")
    table.add_column("Symbol")
    table.add_column("Exchange")
    table.add_column("Score", justify="right")
    table.add_column("Funding/8h", justify="right")
    table.add_column("Persistence", justify="right")
    table.add_column("APY%", justify="right")
    table.add_column("Break-even", justify="right")
    table.add_column("Regime")

    for i, s in enumerate(top_scores, 1):
        apy_str = f"{s.estimated_apy * 100:.2f}%" if s.estimated_apy > 0 else "N/A"
        be_str = f"{s.break_even_days:.0f}d" if s.break_even_days > 0 else "∞"
        table.add_row(
            str(i),
            s.symbol,
            s.exchange,
            f"{s.score:.4f}",
            f"{s.funding_mean * 100:.5f}%",
            f"{s.persistence:.1%}",
            apy_str,
            be_str,
            s.regime,
        )

    console.print(table)


@cli.command("arb-opportunities")
@click.option("--min-spread", default=0.0001, help="Minimum funding differential to flag")
@click.pass_context
def arb_opportunities(ctx: click.Context, min_spread: float) -> None:
    """Show cross-exchange arbitrage opportunities."""
    db_path = ctx.obj["db_path"]

    binance_data = query_all_latest(db_path, exchange="binance")
    hyperliquid_data = query_all_latest(db_path, exchange="hyperliquid")

    # Index by symbol
    binance_map = {r["symbol"]: r for r in binance_data}
    hyperliquid_map = {r["symbol"]: r for r in hyperliquid_data}

    # Find common symbols
    common = set(binance_map.keys()) & set(hyperliquid_map.keys())

    opportunities = []
    for symbol in common:
        b_rate = binance_map[symbol]["funding_rate"]
        h_rate = hyperliquid_map[symbol]["funding_rate"]
        differential = abs(b_rate - h_rate)

        if differential >= min_spread:
            opportunities.append({
                "symbol": symbol,
                "binance_rate": b_rate,
                "hyperliquid_rate": h_rate,
                "differential": differential,
                "direction": "Binance→Hyperliquid" if b_rate > h_rate else "Hyperliquid→Binance",
            })

    if not opportunities:
        console.print("[yellow]No cross-exchange opportunities found above threshold.[/]")
        return

    # Sort by differential descending
    opportunities.sort(key=lambda x: x["differential"], reverse=True)

    table = Table(title="Cross-Exchange Arbitrage Opportunities")
    table.add_column("Symbol")
    table.add_column("Direction")
    table.add_column("Binance/8h", justify="right")
    table.add_column("Hyperliquid/8h", justify="right")
    table.add_column("Diff/8h", justify="right")
    table.add_column("Diff APY%", justify="right")

    for opp in opportunities:
        diff_apy = opp["differential"] * 1095 * 100  # annualized percentage
        table.add_row(
            opp["symbol"],
            opp["direction"],
            f"{opp['binance_rate'] * 100:.5f}%",
            f"{opp['hyperliquid_rate'] * 100:.5f}%",
            f"{opp['differential'] * 100:.5f}%",
            f"{diff_apy:.2f}%",
        )

    console.print(table)


@cli.command()
@click.option("--symbol", "-s", required=True, help="Symbol to analyze")
@click.option("--exchange", "-e", default="binance", help="Exchange")
@click.option("--days", "-d", default=30, help="Days of history")
@click.pass_context
def history(ctx: click.Context, symbol: str, exchange: str, days: int) -> None:
    """Show funding rate history for a symbol."""
    db_path = ctx.obj["db_path"]

    rows = query_funding_history(db_path, symbol, exchange, days)
    if not rows:
        console.print(f"[yellow]No history for {symbol} on {exchange}[/]")
        return

    table = Table(title=f"Funding History: {symbol} on {exchange} (last {days}d)")
    table.add_column("Timestamp")
    table.add_column("Funding Rate", justify="right")
    table.add_column("Mark Price", justify="right")

    for row in rows:
        ts = row["timestamp"][:19] if row["timestamp"] else "?"
        rate_str = f"{row['funding_rate'] * 100:.5f}%"
        mark = f"{row['mark_price']:.2f}" if row.get("mark_price") else "N/A"
        table.add_row(ts, rate_str, mark)

    console.print(table)
