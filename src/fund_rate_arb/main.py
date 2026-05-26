"""Server entry point: polling loop, signal detection, TG notification."""

from __future__ import annotations

import asyncio
import logging
import os
import signal as sig

from fund_rate_arb.collectors.binance import BinanceCollector
from fund_rate_arb.collectors.hyperliquid import HyperliquidCollector
from fund_rate_arb.db import init_db, insert_funding_rates, insert_oi_snapshots, insert_spread_data
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
    logger.info("Threshold: %.1f%% APY, Max spread: %.1f bps", APY_THRESHOLD, MIN_SPREAD_BPS)

    try:
        await scheduler.run(
            hl_callback=lambda: scan_exchange(hl_collector, DB_PATH),
            bn_callback=lambda: scan_exchange(bn_collector, DB_PATH),
        )
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutdown complete")


def run() -> None:
    """Sync wrapper for console_scripts entry point."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
