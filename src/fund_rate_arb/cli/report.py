"""Generate funding rate report — positive funding, APY > threshold."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from fund_rate_arb.models.funding import FundingRate, SpreadData
from fund_rate_arb.scoring.fee_model import annualized_funding_apy
from fund_rate_arb.signal.detector import (
    BINANCE_MAKER, BINANCE_TAKER, HL_MAKER, HL_TAKER,
    Signal, _calc_basis, calc_cost_pct, rank_signals,
)
from fund_rate_arb.data.alpha_prices import get_alpha_prices

console = Console()


def generate_report(
    db_path: str = "fund_rate_arb.db",
    min_apy: float = 10.0,
    output: str | None = None,
) -> list[dict]:
    """Generate report with unified scoring, basis, OI USD."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Latest funding rates per symbol/exchange
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

    oi_rows = conn.execute('''
        SELECT symbol, exchange, open_interest FROM oi_snapshots
        WHERE timestamp = (SELECT MAX(timestamp) FROM oi_snapshots)
    ''').fetchall()
    spread_rows = conn.execute('''
        SELECT symbol, exchange, spread_bps, bid, ask FROM spread_data
        WHERE timestamp = (SELECT MAX(timestamp) FROM spread_data)
    ''').fetchall()

    # 72h history for each symbol
    cutoff = int(time.time() - 72 * 3600)
    cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()

    conn.close()

    oi_map = {(r["symbol"], r["exchange"]): r["open_interest"] for r in oi_rows}

    # Build FundingRate + SpreadData objects for signal pipeline
    rates = []
    for r in rows:
        rates.append(FundingRate(
            symbol=r["symbol"], exchange=r["exchange"],
            timestamp=datetime.now(timezone.utc),
            funding_rate=r["funding_rate"],
            mark_price=r["mark_price"] or 0,
            index_price=r["index_price"] or 0,
        ))

    spreads = []
    for r in spread_rows:
        spreads.append(SpreadData(
            symbol=r["symbol"], exchange=r["exchange"],
            timestamp=datetime.now(timezone.utc),
            bid=r["bid"] or 0, ask=r["ask"] or 0,
            spread_bps=r["spread_bps"] or 0,
        ))

    # Convert OI contracts to USD using mark price
    oi_usd_map = {}
    for (sym, ex), contracts in oi_map.items():
        fr = next((f for f in rates if f.symbol == sym and f.exchange == ex), None)
        if fr:
            oi_usd_map[sym] = contracts * (fr.mark_price or 0)

    # Detect signals with OI filter
    signals = []
    for fr in rates:
        spread = next((s for s in spreads if s.exchange == fr.exchange and s.symbol == fr.symbol), None)
        if not spread:
            continue

        oi_contracts = oi_map.get((fr.symbol, fr.exchange), 0) or 0
        oi_value = oi_contracts * (fr.mark_price or 0)

        maker = BINANCE_MAKER if fr.exchange == "binance" else HL_MAKER
        taker = BINANCE_TAKER if fr.exchange == "binance" else HL_TAKER
        interval_h = 8 if fr.exchange == "binance" else 1
        intervals_per_year = 365 * 3 if fr.exchange == "binance" else 365 * 24

        apy_gross_decimal = annualized_funding_apy(fr.funding_rate, intervals_per_year=intervals_per_year)
        apy_gross = apy_gross_decimal * 100
        cost = calc_cost_pct(spread.spread_bps, maker, taker)
        apy_net = apy_gross - cost

        if apy_net >= min_apy and spread.spread_bps <= 10.0:
            basis = _calc_basis(fr, spread)
            sig = Signal(
                exchange="BN" if fr.exchange == "binance" else "HL",
                symbol=fr.symbol.removesuffix("USDT"),
                apy_net=round(apy_net, 2),
                apy_gross=round(apy_gross, 2),
                cost=round(cost, 2),
                basis_pct=round(basis, 4),
                spread_bps=round(spread.spread_bps, 1),
                interval_h=interval_h,
                oi_usd=oi_value,
            )
            signals.append(sig)

    # Enrich with 72h history
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    history_map = {}
    oi_history_map = {}
    for sig in signals:
        sym_usdt = sig.symbol + "USDT"
        ex_filter = "binance" if sig.exchange == "BN" else "hyperliquid"
        f_rows = conn.execute(
            "SELECT funding_rate FROM funding_rates "
            "WHERE symbol = ? AND exchange = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (sym_usdt, ex_filter, cutoff_iso),
        ).fetchall()
        if f_rows:
            history_map[(sig.exchange, sig.symbol)] = [r[0] for r in f_rows]
        oi_rows_hist = conn.execute(
            "SELECT open_interest FROM oi_snapshots "
            "WHERE symbol = ? AND exchange = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (sym_usdt, ex_filter, cutoff_iso),
        ).fetchall()
        if oi_rows_hist:
            oi_history_map[sig.symbol] = [r[0] for r in oi_rows_hist]
    conn.close()

    ranked = rank_signals(signals, history_map, oi_map=oi_history_map)
    ranked.sort(key=lambda s: s.unified_score, reverse=True)

    # Fetch Alpha spot prices
    alpha_prices = get_alpha_prices(db_path)

    results = []
    for s in ranked:
        sym_usdt = s.symbol + "USDT"
        ex_full = "binance" if s.exchange == "BN" else "hyperliquid"
        mark_row = next((f for f in rates if f.symbol == sym_usdt and f.exchange == ex_full), None)
        mark_price = mark_row.mark_price if mark_row else 0
        index_price = mark_row.index_price if mark_row else 0

        spot_sym = s.symbol + "on"
        spot_price = alpha_prices.get(spot_sym, 0)

        results.append({
            "symbol": s.symbol,
            "exchange": ex_full,
            "exchange_short": s.exchange,
            "apy_net": s.apy_net,
            "apy_gross": s.apy_gross,
            "cost": s.cost,
            "basis_pct": s.basis_pct,
            "spread_bps": s.spread_bps,
            "quality_score": s.quality_score,
            "unified_score": s.unified_score,
            "score_weekly": s.score_weekly,
            "positive_ratio": s.positive_ratio_72h,
            "avg_rate_72h": s.avg_rate_72h,
            "oi_usd": s.oi_usd,
            "interval_h": s.interval_h,
            "mark_price": mark_price,
            "index_price": index_price,
            "spot_price": spot_price,
        })

    return results


def display_table(results: list[dict]) -> None:
    """Display results as a rich table with unified scoring."""
    table = Table(title=f"Candidates: APY Net > {results[0]['apy_net'] if results else 10:.0f}% ({len(results)} found)")
    table.add_column("#")
    table.add_column("Symbol")
    table.add_column("Ex")
    table.add_column("APY Net%", justify="right")
    table.add_column("Basis%", justify="right")
    table.add_column("Spread", justify="right")
    table.add_column("OI USD", justify="right")
    table.add_column("Spot $", justify="right")
    table.add_column("Q Score", justify="right")
    table.add_column("Pos%", justify="right")
    table.add_column("Unified", justify="right")

    for i, r in enumerate(results, 1):
        oi_str = f"${r['oi_usd']/1e6:.1f}M" if r["oi_usd"] >= 1e6 else f"${r['oi_usd']/1e3:.0f}K" if r["oi_usd"] else "N/A"
        spot_str = f"${r['spot_price']:.2f}" if r["spot_price"] else "—"
        table.add_row(
            str(i), r["symbol"], r["exchange_short"],
            f"{r['apy_net']:.1f}%",
            f"{r['basis_pct']:+.4f}%",
            f"{r['spread_bps']:.1f}bp",
            oi_str,
            spot_str,
            f"{r['quality_score']:.3f}",
            f"{r['positive_ratio']*100:.0f}%",
            f"{r['unified_score']:.2f}",
        )

    console.print(table)


def save_report(results: list[dict], path: str, compact: bool = False) -> None:
    """Save report in Telegram-friendly format."""
    ts = datetime.now(timezone.utc)
    timestamp = ts.strftime("%Y-%m-%d %H:%M UTC")
    time_short = ts.strftime("%H:%M UTC")

    if compact:
        lines = [
            f"📊 Funding — {time_short} | APY Net > {results[0]['apy_net'] if results else 10:.0f}%",
            "",
        ]
        for i, r in enumerate(results, 1):
            oi_str = f" OI ${r['oi_usd']/1e6:.1f}M" if r["oi_usd"] >= 1e6 else ""
            spot_str = f" | Spot ${r['spot_price']:.2f}" if r["spot_price"] else ""
            lines.append(
                f"**{i}. {r['symbol']}** @{r['exchange_short']} — "
                f"**{r['apy_net']:.1f}%** | Basis {r['basis_pct']:+.3f}% | "
                f"Q {r['quality_score']:.3f} | Unified {r['unified_score']:.2f}{spot_str}{oi_str}"
            )
        lines.extend(["", f"_{len(results)} candidates_"])
    else:
        lines = [
            f"📊 Funding Rate Report — {timestamp}",
            "",
            "Criteria: Positive funding, APY Net > threshold (after fees)",
            "",
        ]
        for i, r in enumerate(results, 1):
            oi_str = f"${r['oi_usd']:,.0f}" if r["oi_usd"] else "N/A"
            spot_str = f" | Spot: ${r['spot_price']:.2f}" if r["spot_price"] else ""
            lines.append(
                f"{i}. **{r['symbol']}** @ {r['exchange_short']} — "
                f"**{r['apy_net']:.1f}% APY Net** (gross {r['apy_gross']:.1f}%, cost {r['cost']:.2f}%)"
            )
            lines.append(
                f"   Basis: {r['basis_pct']:+.4f}%  |  Spread: {r['spread_bps']:.1f}bps  |  "
                f"OI: {oi_str}  |  Mark: ${r['mark_price']:.2f}{spot_str}"
            )
            lines.append(
                f"   Q Score: {r['quality_score']:.4f}  |  "
                f"Positive 72h: {r['positive_ratio']*100:.0f}%  |  "
                f"Unified: {r['unified_score']:.2f}  |  "
                f"Funding/interval: {r['avg_rate_72h']*100:.5f}%"
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
@click.option("--min-apy", default=10.0, help="Minimum APY net % threshold")
@click.option("--output", "-o", default="reports/funding-report-latest.md", help="Output path")
@click.option("--compact/--full", default=False, help="Compact single-line format")
def cli(db: str, min_apy: float, output: str, compact: bool) -> None:
    """Generate funding rate report with unified scoring."""
    results = generate_report(db, min_apy, output)
    if not results:
        console.print(f"[yellow]No candidates with APY net > {min_apy}%[/]")
        return
    display_table(results)
    save_report(results, output, compact=compact)


if __name__ == "__main__":
    cli()
