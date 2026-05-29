"""Funding rate carry strategy — SHORT perp + LONG spot on same underlying."""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from datetime import datetime, timezone

from fund_rate_arb.config import Underlying
from fund_rate_arb.data.monitors import (
    compute_basis_drift,
    detect_funding_regime_shift,
    detect_oi_spike,
)
from fund_rate_arb.data.retriever import query_funding_window, query_oi_window
from fund_rate_arb.models.funding import (
    CarryPosition,
    ExitSignal,
    MarketData,
)
from fund_rate_arb.risk.exit_engine import ExitRuleEngine
from fund_rate_arb.signal.detector import Signal
from fund_rate_arb.strategies.base import BaseStrategy, StrategyResult

logger = logging.getLogger(__name__)


class FundingCarry(BaseStrategy):
    """SHORT perpetual + LONG spot on same underlying, collect funding."""

    def __init__(
        self,
        perp_executor,
        spot_executor,
        exit_engine: ExitRuleEngine,
        max_positions: int = 5,
        min_apy: float = 15.0,
        db_path: str = "fund_rate_arb.db",
        notional_per_leg: float = 200.0,
        allocator=None,
        min_oi_usd: float = 5_000_000,
    ):
        from fund_rate_arb.execution.allocator import Allocator

        self.perp_executor = perp_executor
        self.spot_executor = spot_executor
        self.exit_engine = exit_engine
        self.max_positions = max_positions
        self.min_apy = min_apy
        self.db_path = db_path
        self.notional_per_leg = notional_per_leg
        self.allocator = allocator or Allocator(
            total_capital=notional_per_leg * max_positions * 2,
            max_concurrent=max_positions,
            notional_per_leg=notional_per_leg * 2,
        )
        self.min_oi_usd = min_oi_usd

    @property
    def name(self) -> str:
        return "funding_carry"

    # Backwards-compatible alias for BaseStrategy ABC
    async def open_position(
        self,
        signal: Signal,
        db_path: str,
        side: str = "SHORT",
    ) -> CarryPosition | None:
        """Open a single-leg position (backwards compat)."""
        return await self._open_single_leg(signal, db_path, side=side)

    async def tick(self, db_path: str) -> StrategyResult:
        result = StrategyResult()
        self.db_path = db_path

        open_positions = self._load_open_positions(db_path)
        self._sync_allocator(open_positions)

        # Monitor and exit positions with critical signals
        for pos in open_positions:
            exits = await self.monitor_position(pos, db_path)
            for exit_sig in exits:
                if exit_sig.severity == "critical":
                    closed = await self.exit_position(pos, exit_sig.rule_type, db_path)
                    if closed:
                        result.positions_closed += 1

        # Fetch signals and select paired trades
        new_signals = await self._fetch_signals(db_path)
        pairs = self.select(new_signals, open_positions)

        for underlying in pairs:
            if not self.allocator.can_allocate():
                break
            pos = await self.open_paired_position(underlying, db_path)
            if pos is not None:
                self.allocator.allocate()
                self._save_position(pos, db_path)
                result.positions_opened += 1

        result.signals_generated = len(new_signals)
        return result

    def select(
        self,
        signals: list[Signal],
        open_positions: list[CarryPosition],
    ) -> list[Underlying]:
        """Return underlyings eligible for paired SHORT perp + LONG spot."""
        from fund_rate_arb.config import UNDERLYINGS

        signal_map = {s.symbol: s for s in signals}
        existing_symbols = {
            p.symbol.replace("/USDT:USDT", "").replace("USDT", "")
            for p in open_positions
        }
        if not self.allocator.can_allocate():
            return []

        # Only underlyings with BOTH perp and spot, above APY threshold
        candidates = []
        for u in UNDERLYINGS:
            if u.binance_f is None or u.binance_spot is None:
                continue
            perp_symbol = u.binance_f.removesuffix("USDT")
            if perp_symbol in existing_symbols:
                continue
            sig = signal_map.get(perp_symbol)
            if sig is None or sig.apy_net < self.min_apy:
                continue
            candidates.append((u, sig.unified_score))

        # Sort by unified score desc, take top N pairs
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [u for u, _ in candidates[:self.allocator.available_slots]]

    async def _open_single_leg(
        self,
        signal: Signal,
        db_path: str,
        side: str = "SHORT",
    ) -> CarryPosition | None:
        """Open a single-leg position (for backwards compat / testing)."""
        mark_price = self._get_mark_price(signal.symbol + "USDT", db_path)
        if mark_price <= 0:
            logger.warning("No mark price for %s, skipping", signal.symbol)
            return None

        executor = self.perp_executor if side == "SHORT" else self.spot_executor
        pos = executor.open_position(signal, mark_price=mark_price, side=side)
        if pos:
            logger.info("Opened %s %s: %.4f @ %.2f", side, pos.symbol, pos.contracts, pos.entry_price)
        return pos

    async def open_paired_position(
        self,
        underlying: Underlying,
        db_path: str,
    ) -> CarryPosition | None:
        """Open SHORT perp + LONG spot simultaneously."""
        execution_id = str(uuid.uuid4())
        perp_symbol = underlying.binance_f
        spot_symbol = underlying.binance_spot

        # Get mark price from DB
        mark_price = self._get_mark_price(perp_symbol, db_path)
        if mark_price <= 0:
            logger.warning("No mark price for %s, skipping", underlying.ticker)
            return None

        # Get spot price from DB (use same mark as approximation)
        spot_price = self._get_mark_price(spot_symbol, db_path) or mark_price

        contracts = self.notional_per_leg / mark_price
        spot_amount = self.notional_per_leg / spot_price

        # Open SHORT perp
        perp_sig = Signal(
            exchange="BN", symbol=underlying.ticker,
            apy_net=0.0, apy_gross=0.0, cost=0.0,
            basis_pct=0.0, spread_bps=0.0, interval_h=8,
        )
        perp_pos = self.perp_executor.open_position(
            perp_sig, execution_id=f"{execution_id}_perp",
            mark_price=mark_price, side="SHORT",
        )
        if not perp_pos:
            logger.error("Perp leg failed for %s", underlying.ticker)
            return None

        # Open LONG spot
        spot_sig = Signal(
            exchange="BN", symbol=underlying.ticker,
            apy_net=0.0, apy_gross=0.0, cost=0.0,
            basis_pct=0.0, spread_bps=0.0, interval_h=8,
        )
        spot_pos = self.spot_executor.open_position(
            spot_sig, execution_id=f"{execution_id}_spot",
            mark_price=spot_price, side="LONG",
        )
        if not spot_pos:
            logger.error("Spot leg failed for %s, closing perp", underlying.ticker)
            self.perp_executor.close_position(perp_pos, "spot_leg_failed")
            return None

        # Return combined position record
        pos = CarryPosition(
            execution_id=execution_id,
            strategy_name="funding_carry",
            symbol=f"{underlying.ticker}/USDT",
            exchange="binance", side="NEUTRAL",
            contracts=round(contracts, 4),
            entry_price=mark_price,
            entry_basis=0.0, entry_cost=0.0, cumulative_funding=0.0,
            notional_usdt=self.notional_per_leg * 2,
            opened_at=datetime.now(timezone.utc).isoformat(),
            max_break_even_days=10, status="Open",
        )
        logger.info(
            "Opened paired %s: SHORT %.4f perp @ %.2f + LONG %.4f spot @ %.2f",
            underlying.ticker, contracts, mark_price, spot_amount, spot_price,
        )
        return pos

    def _get_mark_price(self, symbol: str, db_path: str) -> float:
        """Get latest mark price from DB."""
        from fund_rate_arb.db import get_connection

        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT mark_price FROM funding_rates "
                "WHERE symbol = ? AND exchange = 'binance' "
                "ORDER BY timestamp DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            return float(row[0]) if row and row[0] else 0
        finally:
            conn.close()

    def _get_index_price(self, symbol: str, db_path: str) -> float:
        """Get latest index price from DB."""
        from fund_rate_arb.db import get_connection

        conn = get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT index_price FROM funding_rates "
                "WHERE symbol = ? AND exchange = 'binance' "
                "ORDER BY timestamp DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            return float(row[0]) if row and row[0] else 0
        finally:
            conn.close()

    async def monitor_position(
        self,
        position: CarryPosition,
        db_path: str,
    ) -> list[ExitSignal]:
        symbol = position.symbol.split("/")[0] + "USDT"
        exchange = "binance"

        funding_48h = query_funding_window(db_path, symbol, exchange, 48)
        oi_8h = query_oi_window(db_path, symbol, exchange, 8)

        live_mark = self._get_mark_price(symbol, db_path)
        live_index = self._get_index_price(symbol, db_path)

        market = MarketData(
            symbol=symbol,
            exchange=exchange,
            current_mark=live_mark or position.entry_price,
            current_index=live_index or position.entry_price,
            current_basis=(
                (live_mark - live_index) / live_index
                if live_mark and live_index
                else position.entry_basis
            ),
            funding_history_48h=funding_48h,
            oi_window_8h=oi_8h,
        )

        exits = self.exit_engine.check_all(position, market)

        basis_drift = compute_basis_drift(
            market.current_mark, market.current_index, position.entry_basis
        )
        if basis_drift > 0.02:
            exits.append(
                ExitSignal(
                    position_execution_id=position.execution_id,
                    rule_type="basis_drift",
                    severity="critical",
                    message=f"Basis drift {basis_drift:.4f} exceeds 2% threshold",
                )
            )

        oi_spike = detect_oi_spike(oi_8h)
        if oi_spike.triggered:
            exits.append(
                ExitSignal(
                    position_execution_id=position.execution_id,
                    rule_type="oi_spike",
                    severity="critical",
                    message=oi_spike.message,
                )
            )

        if funding_48h:
            regime_shift = detect_funding_regime_shift(
                funding_48h[-3:],
                funding_48h[:-3],
            )
            if regime_shift.triggered:
                exits.append(
                    ExitSignal(
                        position_execution_id=position.execution_id,
                        rule_type="regime_shift",
                        severity="critical",
                        message=regime_shift.message,
                    )
                )

        for e in exits:
            logger.info(
                "Exit signal for %s: [%s] %s", position.symbol, e.severity, e.message
            )

        return exits

    async def exit_position(
        self,
        position: CarryPosition,
        reason: str,
        db_path: str,
    ) -> bool:
        perp_ok = self.perp_executor.close_position(position, reason)
        spot_ok = self.spot_executor.close_position(position, reason)
        if perp_ok and spot_ok:
            position.status = "Closed"
            position.close_reason = reason
            self._update_position_status(position, db_path)
            self.allocator.release()
            logger.info("Closed %s: %s", position.symbol, reason)
        return perp_ok and spot_ok

    def _sync_allocator(self, open_positions: list[CarryPosition]) -> None:
        """Sync allocator slot count from DB open positions."""
        self.allocator._used_slots = len(open_positions)

    def _load_open_positions(self, db_path: str) -> list[CarryPosition]:
        from fund_rate_arb.db import query_open_positions_by_strategy

        rows = query_open_positions_by_strategy(db_path, self.name)
        return [
            CarryPosition(
                execution_id=r["execution_id"] or "",
                strategy_name=r["strategy_name"] or self.name,
                symbol=r["symbol"],
                exchange=r["exchange"],
                side=r["side"],
                contracts=r["contracts"],
                entry_price=r["entry_price"],
                entry_basis=r.get("entry_basis", 0.0) or 0.0,
                entry_cost=0.0,
                cumulative_funding=r.get("cumulative_funding", 0.0) or 0.0,
                notional_usdt=r["contracts"] * r["entry_price"],
                opened_at=r["opened_at"],
                max_break_even_days=r.get("max_break_even_days", 10) or 10,
                status=r["status"],
            )
            for r in rows
        ]

    def _save_position(self, position: CarryPosition, db_path: str) -> None:
        from fund_rate_arb.db import insert_strategy_position

        row = (
            position.symbol,
            position.exchange,
            position.side,
            position.contracts,
            position.entry_price,
            position.entry_price,
            0.0,
            position.notional_usdt,
            1,
            position.opened_at,
            position.status,
            self.name,
            position.entry_basis,
            position.cumulative_funding,
            position.max_break_even_days,
        )
        position.execution_id = insert_strategy_position(db_path, row)

    def _update_position_status(
        self,
        position: CarryPosition,
        db_path: str,
    ) -> None:
        from fund_rate_arb.db import close_strategy_position

        close_strategy_position(
            db_path,
            position.execution_id,
            position.close_reason or "",
        )

    async def _fetch_signals(self, db_path: str) -> list[Signal]:
        """Re-run signal detection on latest data, enriched with unified scores."""
        from fund_rate_arb.data.alpha_prices import get_alpha_prices
        from fund_rate_arb.db import get_connection
        from fund_rate_arb.models.funding import FundingRate, SpreadData
        from fund_rate_arb.signal.detector import detect_signals, rank_signals

        conn = get_connection(db_path)
        try:
            conn.row_factory = sqlite3.Row

            # Latest funding rates (binance)
            data = conn.execute(
                "SELECT * FROM funding_rates "
                "WHERE exchange = 'binance' "
                "AND timestamp = (SELECT MAX(timestamp) FROM funding_rates WHERE exchange = 'binance')"
            ).fetchall()

            if not data:
                return []

            # Real spread data from spread_data table
            spreads_raw = conn.execute(
                "SELECT * FROM spread_data "
                "WHERE exchange = 'binance' "
                "AND timestamp = (SELECT MAX(timestamp) FROM spread_data WHERE exchange = 'binance')"
            ).fetchall()

            # OI snapshot for filtering
            oi_raw = conn.execute(
                "SELECT symbol, open_interest FROM oi_snapshots "
                "WHERE exchange = 'binance' "
                "AND timestamp = (SELECT MAX(timestamp) FROM oi_snapshots WHERE exchange = 'binance')"
            ).fetchall()
            oi_map = {r["symbol"].removesuffix("USDT"): r["open_interest"] for r in oi_raw}

            rates = []
            for row in data:
                rates.append(
                    FundingRate(
                        symbol=row["symbol"],
                        exchange="binance",
                        timestamp=datetime.now(timezone.utc),
                        funding_rate=row["funding_rate"],
                        mark_price=row["mark_price"] or 0,
                        index_price=row["index_price"] or 0,
                    )
                )

            spreads = []
            for row in spreads_raw:
                spreads.append(
                    SpreadData(
                        symbol=row["symbol"],
                        exchange="binance",
                        timestamp=datetime.now(timezone.utc),
                        bid=row["bid"],
                        ask=row["ask"],
                        spread_bps=row["spread_bps"],
                    )
                )

            # Detect signals with real spread + OI
            signals = detect_signals(
                rates, spreads, apy_threshold=self.min_apy,
                min_oi_usd=self.min_oi_usd,
                oi_map=oi_map,
            )

            # Enrich with Alpha spot prices for equities
            alpha_prices = get_alpha_prices(db_path)
            for sig in signals:
                spot_sym = sig.symbol + "on"
                if spot_sym in alpha_prices:
                    sig.spot_price = alpha_prices[spot_sym]

            # 72h funding + OI history for ranking
            cutoff = int(time.time() - 72 * 3600)
            cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat()
            history_map: dict[tuple[str, str], list[float]] = {}
            oi_history_map: dict[str, list[float]] = {}
            for sig in signals:
                symbol_usdt = sig.symbol + "USDT"
                rows = conn.execute(
                    "SELECT funding_rate FROM funding_rates "
                    "WHERE symbol = ? AND exchange = 'binance' "
                    "AND timestamp >= ? ORDER BY timestamp ASC",
                    (symbol_usdt, cutoff_iso),
                ).fetchall()
                if rows:
                    history_map[("BN", sig.symbol)] = [r[0] for r in rows]

                oi_rows = conn.execute(
                    "SELECT open_interest FROM oi_snapshots "
                    "WHERE symbol = ? AND exchange = 'binance' "
                    "AND timestamp >= ? ORDER BY timestamp ASC",
                    (symbol_usdt, cutoff_iso),
                ).fetchall()
                if oi_rows:
                    oi_history_map[sig.symbol] = [r[0] for r in oi_rows]

        finally:
            conn.close()

        return rank_signals(signals, history_map, oi_map=oi_history_map)
