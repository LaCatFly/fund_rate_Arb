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
    get_connection,
)
from fund_rate_arb.collectors import BinanceCollector, HyperliquidCollector
from fund_rate_arb.scoring import compute_quality_score
from fund_rate_arb.cli.report import generate_report, display_table, save_report

console = Console()


@click.group()
@click.option(
    "--db", "db_path", default="fund_rate_arb.db", help="SQLite database path"
)
@click.pass_context
def cli(ctx: click.Context, db_path: str) -> None:
    """Funding rate arbitrage screener for Binance and Hyperliquid."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path
    init_db(db_path)


@cli.command()
@click.option("--min-apy", default=10.0, help="Minimum APY% threshold")
@click.option(
    "--output", "-o", default="reports/funding-report-latest.md", help="Output path"
)
@click.option("--compact/--full", default=False, help="Compact single-line format")
@click.pass_context
def report(ctx: click.Context, min_apy: float, output: str, compact: bool) -> None:
    """Generate funding rate report for symbols above APY threshold."""
    db_path = ctx.obj["db_path"]
    results = generate_report(db_path, min_apy, output)
    if not results:
        console.print(f"[yellow]No candidates with APY > {min_apy}%[/]")
        return
    display_table(results)
    save_report(results, output, compact=compact)


@cli.command()
@click.option(
    "--exchange",
    "-e",
    type=click.Choice(["binance", "hyperliquid", "all"]),
    default="all",
    help="Exchange to fetch",
)
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
@click.option(
    "--min-spread", default=0.0001, help="Minimum funding differential to flag"
)
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
        # Convert Hyperliquid hourly rate to per-8h equivalent
        h_rate_per_8h = h_rate * 8
        differential = abs(b_rate - h_rate_per_8h)

        if differential >= min_spread:
            opportunities.append(
                {
                    "symbol": symbol,
                    "binance_rate": b_rate,
                    "hyperliquid_rate": h_rate,
                    "hyperliquid_rate_per_8h": h_rate_per_8h,
                    "differential": differential,
                    "direction": "Binance→Hyperliquid"
                    if b_rate > h_rate_per_8h
                    else "Hyperliquid→Binance",
                }
            )

    if not opportunities:
        console.print(
            "[yellow]No cross-exchange opportunities found above threshold.[/]"
        )
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
        diff_apy = (
            opp["differential"] * 1095 * 100
        )  # annualized percentage (per-8h basis)
        table.add_row(
            opp["symbol"],
            opp["direction"],
            f"{opp['binance_rate'] * 100:.5f}%",
            f"{opp['hyperliquid_rate_per_8h'] * 100:.5f}%",
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


@cli.command()
@click.option("--min-apy", default=10.0, help="Minimum net APY% threshold")
@click.option("--max-spread", default=10.0, help="Maximum spread in bps")
@click.option("--output", "-o", default=None, help="Save output to file")
@click.pass_context
def signals(
    ctx: click.Context, min_apy: float, max_spread: float, output: str | None
) -> None:
    """Fetch data, detect & rank funding signals, print compact TG table."""
    import asyncio, sqlite3, statistics, time
    from datetime import datetime, timezone

    async def _run():
        all_funding, all_spreads = [], []
        for coll in [BinanceCollector(), HyperliquidCollector()]:
            console.print(f"[blue]Fetching {coll.exchange_name}...[/]")
            f, o, s = await coll.fetch_all()
            all_funding.extend(f)
            all_spreads.extend(s)
            console.print(f"  [green]✓ {len(f)} rates, {len(s)} spreads[/]")

        from fund_rate_arb.signal.detector import detect_signals

        signals = detect_signals(
            all_funding, all_spreads, apy_threshold=min_apy, max_spread_bps=max_spread
        )

        if not signals:
            console.print("[yellow]No signals found[/]")
            return

        # 72h history
        conn = get_connection(ctx.obj["db_path"])
        conn.row_factory = sqlite3.Row
        cutoff = int(time.time() - 72 * 3600)
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
        for s in signals:
            ex = "binance" if s.exchange == "BN" else "hyperliquid"
            rows = conn.execute(
                "SELECT funding_rate FROM funding_rates "
                "WHERE symbol=? AND exchange=? AND timestamp >= ? ORDER BY timestamp ASC",
                (s.symbol + "USDT", ex, cutoff_iso),
            ).fetchall()
            rates = [r["funding_rate"] for r in rows]
            if len(rates) >= 2:
                s.avg_rate_72h = round(statistics.mean(rates), 8)
                s.std_rate_72h = round(statistics.stdev(rates), 8)
                s.positive_ratio_72h = round(
                    sum(1 for r in rates if r > 0) / len(rates), 4
                )
            # Score
            cost_w = s.spread_bps / 100 * 52
            net_w = s.apy_gross - cost_w
            p = min(max(0.6 + s.positive_ratio_72h * 0.4, 0.4), 1.0)
            if s.avg_rate_72h and s.std_rate_72h > 0:
                vr = min(s.std_rate_72h / abs(s.avg_rate_72h), 1.0)
            else:
                vr = 0.3
            vp = 1.0 - vr * 0.5
            st = 1.05 if s.interval_h == 8 else 0.95
            s.score_weekly = round(net_w * p * vp * st, 1)
        conn.close()

        signals.sort(key=lambda s: s.score_weekly, reverse=True)

        # Print compact table
        from fund_rate_arb.tg.formatter import format_signals
        from fund_rate_arb.tg.models import Signal as TGSignal

        tg_signals = [
            TGSignal(
                exchange=s.exchange,
                symbol=s.symbol,
                apy_net=s.apy_net,
                apy_gross=s.apy_gross,
                cost=s.cost,
                basis_pct=s.basis_pct,
                spread_bps=s.spread_bps,
                interval_h=s.interval_h,
                avg_rate_72h=s.avg_rate_72h,
                std_rate_72h=s.std_rate_72h,
                positive_ratio_72h=s.positive_ratio_72h,
                score_daily=0.0,
                score_weekly=s.score_weekly,
            )
            for s in signals
        ]
        text = format_signals(tg_signals)
        console.print(text)

        if output:
            from pathlib import Path

            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(text + "\n")
            console.print(f"[green]Saved to {output}[/]")

    asyncio.run(_run())


@cli.command()
@click.pass_context
def pm_status(ctx: click.Context) -> None:
    """Show account status, positions, and recent trades."""
    db_path = ctx.obj["db_path"]
    from fund_rate_arb.collectors import get_trading_collector
    from fund_rate_arb.trading.engine import TradingEngine

    collector = get_trading_collector()
    engine = TradingEngine(collector, db_path)
    status = engine.pm_status()

    account = status["account"]
    console.print("[bold cyan]=== Portfolio Margin Account ===[/]")
    console.print(f"  Equity:   [green]{account.total_account_balance:.2f}[/] USDT")
    console.print(f"  Available:[green]{account.available_balance:.2f}[/] USDT")
    console.print(f"  Status:   {account.account_type}")

    # Live positions
    live_positions = status["live_positions"]
    console.print(f"\n[bold cyan]=== Open Positions ({len(live_positions)}) ===[/]")
    if live_positions:
        table = Table()
        table.add_column("Symbol")
        table.add_column("Side")
        table.add_column("Contracts", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("PnL", justify="right")
        table.add_column("Leverage", justify="right")

        for p in live_positions:
            pnl_style = "green" if p["unrealized_pnl"] >= 0 else "red"
            table.add_row(
                p["symbol"],
                p["side"].upper(),
                f"{p['contracts']}",
                f"{p['entry_price']:.4f}",
                f"[{pnl_style}]{p['unrealized_pnl']:.4f}[/]",
                str(p["leverage"]),
            )
        console.print(table)
    else:
        console.print("  [dim]No open positions[/]")

    # Recent trades
    trades = status["recent_trades"]
    console.print(f"\n[bold cyan]=== Recent Trades ({len(trades)}) ===[/]")
    if trades:
        table = Table()
        table.add_column("Time")
        table.add_column("Symbol")
        table.add_column("Side")
        table.add_column("Amount", justify="right")
        table.add_column("Price", justify="right")
        table.add_column("Status")
        table.add_column("Order ID")

        for t in trades:
            ts = t["created_at"][:19] if t["created_at"] else "?"
            table.add_row(
                ts,
                t["symbol"],
                f"{t['position_side'] or ''} {t['side']}".strip().upper(),
                f"{t['amount']}",
                f"{t['average'] or t['price'] or 'N/A'}",
                t["status"],
                t["order_id"],
            )
        console.print(table)
    else:
        console.print("  [dim]No trades recorded[/]")


@cli.command()
@click.option(
    "--symbol", "-s", required=True, help="Symbol to trade (e.g. DOT/USDT:USDT)"
)
@click.option(
    "--side",
    required=True,
    type=click.Choice(["long", "short"]),
    help="Position direction",
)
@click.option("--amount", "-a", type=float, required=True, help="Contract amount")
@click.option("--dry-run", is_flag=True, help="Print order details without executing")
@click.pass_context
def trade(
    ctx: click.Context, symbol: str, side: str, amount: float, dry_run: bool
) -> None:
    """Execute a single futures order."""
    from fund_rate_arb.collectors import get_trading_collector

    collector = get_trading_collector()
    db_path = ctx.obj["db_path"]
    from fund_rate_arb.trading.engine import TradingEngine

    engine = TradingEngine(collector, db_path)

    position_side = side.upper()
    order_side = "buy" if side == "long" else "sell"

    if dry_run:
        ticker = collector.exchange.fetch_ticker(symbol)
        console.print(
            f"[yellow]DRY RUN[/] {position_side} {symbol}: {amount} units @ ~{ticker['last']}"
        )
        notional = amount * ticker["last"]
        console.print(f"  Estimated notional: ${notional:.2f}")
        return

    # Risk check
    can_trade, msg = engine.risk.check_can_trade(collector)
    if not can_trade:
        console.print(f"[red]Risk check failed: {msg}[/]")
        return

    console.print(f"[bold green]Executing[/] {position_side} {symbol}: {amount} units")

    result = collector.place_order(
        symbol=symbol,
        side=order_side,
        amount=amount,
        position_side=position_side,
    )
    console.print(f"  Order ID:  {result.order_id}")
    console.print(f"  Status:    {result.status}")
    console.print(f"  Filled:    {result.filled}")
    console.print(f"  Avg Price: {result.average}")

    engine.record_trade(result)
    console.print("[green]Trade recorded[/]")


@cli.command()
@click.option("--symbol", "-s", required=True, help="Symbol to close")
@click.option(
    "--side",
    required=True,
    type=click.Choice(["long", "short"]),
    help="Position side to close",
)
@click.option(
    "--amount", "-a", type=float, required=True, help="Contract amount to close"
)
@click.pass_context
def close(ctx: click.Context, symbol: str, side: str, amount: float) -> None:
    """Close an existing futures position."""
    from fund_rate_arb.collectors import get_trading_collector

    collector = get_trading_collector()
    db_path = ctx.obj["db_path"]
    from fund_rate_arb.trading.engine import TradingEngine

    engine = TradingEngine(collector, db_path)
    position_side = side.upper()

    console.print(f"[bold red]Closing[/] {position_side} {symbol}: {amount} units")

    result = collector.close_position(
        symbol=symbol,
        amount=amount,
        position_side=position_side,
    )
    console.print(f"  Order ID:  {result.order_id}")
    console.print(f"  Status:    {result.status}")
    console.print(f"  Filled:    {result.filled}")
    console.print(f"  Avg Price: {result.average}")

    engine.record_trade(result)
    console.print("[green]Position closed, trade recorded[/]")


@cli.command()
@click.option(
    "--db", "db_path", default="fund_rate_arb.db", help="SQLite database path"
)
def scan(db_path: str) -> None:
    """Run the continuous signal scanner (polling loop)."""
    import os
    from fund_rate_arb.main import main as scanner_main

    os.environ["DB_PATH"] = db_path
    asyncio.run(scanner_main())


@cli.command("scan-strategy")
@click.option(
    "--db", "db_path", default="fund_rate_arb.db", help="SQLite database path"
)
@click.option("--paper/--live", default=True, help="Paper or live execution")
@click.option("--max-positions", default=5, help="Max concurrent positions")
@click.option("--min-apy", default=15.0, help="Minimum APY threshold")
@click.pass_context
def scan_strategy(
    ctx: click.Context, db_path: str, paper: bool, max_positions: int, min_apy: float
) -> None:
    """Run funding carry strategy loop (single tick for testing)."""
    from fund_rate_arb.db import init_db, migrate_db

    init_db(db_path)
    migrate_db(db_path)

    async def _run():
        if paper:
            from fund_rate_arb.main import run_strategy_tick

            console.print("[blue]Running strategy tick (paper)...[/]")
            await run_strategy_tick(db_path)
        else:
            from fund_rate_arb.collectors import get_trading_collector
            from fund_rate_arb.execution.live import LiveExecutor
            from fund_rate_arb.risk.exit_engine import (
                APYThresholdRule, FundingFlipRule, TimeBasedRule, ExitRuleEngine,
            )
            from fund_rate_arb.strategies.funding_carry import FundingCarry

            collector = get_trading_collector()
            executor = LiveExecutor(collector=collector, notional_per_leg=50.0)
            strategy = FundingCarry(
                executor=executor,
                exit_engine=ExitRuleEngine([
                    TimeBasedRule(max_hold_hours=168),
                    FundingFlipRule(consecutive_neg=3),
                    APYThresholdRule(min_apy=10.0),
                ]),
                max_positions=max_positions,
                min_apy=min_apy,
            )
            console.print("[yellow]Running strategy tick (LIVE)...[/]")
            result = await strategy.tick(db_path)
            console.print(f"  +{result.positions_opened} opened, -{result.positions_closed} closed")
            for err in result.errors:
                console.print(f"  [red]Error: {err}[/]")
        console.print("[green]Strategy tick complete[/]")

    asyncio.run(_run())
