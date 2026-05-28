"""Server entry point: polling loop, signal detection, TG notification."""

from __future__ import annotations

import asyncio
import logging
import os
import signal as sig

from fund_rate_arb.collectors.binance import BinanceCollector
from fund_rate_arb.collectors.hyperliquid import HyperliquidCollector
from fund_rate_arb.db import (
    init_db,
    insert_funding_rates,
    insert_oi_snapshots,
    insert_spread_data,
)
from fund_rate_arb.signal.detector import detect_signals, Signal
from fund_rate_arb.signal.scheduler import PollScheduler

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

APY_THRESHOLD = float(os.environ.get("APY_THRESHOLD", "15.0"))
MIN_SPREAD_BPS = float(os.environ.get("MIN_SPREAD_BPS", "10.0"))
DB_PATH = os.environ.get("DB_PATH", "fund_rate_arb.db")


async def send_to_tg(signals: list[Signal]) -> None:
    from fund_rate_arb.tg.sender import send_signals as tg_send
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
        )
        for s in signals
    ]
    await tg_send(tg_signals)


async def scan_exchange(collector, db_path: str) -> None:
    funding, oi, spreads = await collector.fetch_all()

    insert_funding_rates(db_path, [f.to_db_row() for f in funding])
    insert_oi_snapshots(db_path, [o.to_db_row() for o in oi])
    insert_spread_data(db_path, [s.to_db_row() for s in spreads])

    signals = detect_signals(
        funding,
        spreads,
        apy_threshold=APY_THRESHOLD,
        max_spread_bps=MIN_SPREAD_BPS,
    )

    if signals:
        logger.info("%d signals detected", len(signals))
        try:
            await send_to_tg(signals)
        except Exception:
            logger.exception("TG send failed")


async def run_strategy_tick(db_path: str) -> None:
    """Run FundingCarry strategy: select, execute, monitor, exit."""
    from fund_rate_arb.execution.paper import PaperExecutor
    from fund_rate_arb.risk.exit_engine import (
        APYThresholdRule,
        ExitRuleEngine,
        FundingFlipRule,
        TimeBasedRule,
    )
    from fund_rate_arb.strategies.funding_carry import FundingCarry

    strategy = FundingCarry(
        executor=PaperExecutor(notional_per_leg=200.0),
        exit_engine=ExitRuleEngine(
            [
                TimeBasedRule(max_hold_hours=168),
                FundingFlipRule(consecutive_neg=3),
                APYThresholdRule(min_apy=10.0),
            ]
        ),
        max_positions=5,
        min_apy=APY_THRESHOLD,
    )

    result = await strategy.tick(db_path)
    if result.positions_opened or result.positions_closed:
        logger.info(
            "Strategy: +%d opened, -%d closed, %d signals",
            result.positions_opened,
            result.positions_closed,
            result.signals_generated,
        )
    for err in result.errors:
        logger.error("Strategy error: %s", err)


async def main() -> None:
    init_db(DB_PATH)

    bn_collector = BinanceCollector()
    hl_collector = HyperliquidCollector()

    scheduler = PollScheduler(
        hl_interval=3600,
        bn_interval=28800,
    )

    def _handle_signal():
        logger.info("Shutdown signal received")
        scheduler.stop()

    loop = asyncio.get_running_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, _handle_signal)

    logger.info("Starting funding rate signal scanner")
    logger.info(
        "Threshold: %.1f%% APY, Max spread: %.1f bps", APY_THRESHOLD, MIN_SPREAD_BPS
    )

    async def _bn_scan_with_strategy():
        await scan_exchange(bn_collector, DB_PATH)
        await run_strategy_tick(DB_PATH)

    try:
        await scheduler.run(
            hl_callback=lambda: scan_exchange(hl_collector, DB_PATH),
            bn_callback=lambda: _bn_scan_with_strategy(),
        )
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutdown complete")


def run() -> None:
    """Sync wrapper for console_scripts entry point — delegates to Click CLI."""
    from pathlib import Path
    from dotenv import load_dotenv

    # Load .env from cwd or package parent (supports both dev install and CLI)
    for candidate in [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]:
        if candidate.exists():
            load_dotenv(candidate)
            break

    from fund_rate_arb.cli.main import cli

    cli()


if __name__ == "__main__":
    run()
