"""Funding rate carry strategy — short high-funding perps."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fund_rate_arb.data.monitors import detect_oi_spike, detect_funding_regime_shift
from fund_rate_arb.data.retriever import query_funding_window, query_oi_window
from fund_rate_arb.execution.paper import PaperExecutor
from fund_rate_arb.execution.live import LiveExecutor
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
    """Short the highest-funding-rate perps, collect payments."""

    def __init__(
        self,
        executor: PaperExecutor | LiveExecutor,
        exit_engine: ExitRuleEngine,
        max_positions: int = 5,
        min_apy: float = 15.0,
        db_path: str = "fund_rate_arb.db",
        notional_per_leg: float = 200.0,
    ):
        self.executor = executor
        self.exit_engine = exit_engine
        self.max_positions = max_positions
        self.min_apy = min_apy
        self.db_path = db_path
        self.notional_per_leg = notional_per_leg

    @property
    def name(self) -> str:
        return "funding_carry"

    async def tick(self, db_path: str) -> StrategyResult:
        result = StrategyResult()
        self.db_path = db_path

        open_positions = self._load_open_positions(db_path)

        for pos in open_positions:
            exits = await self.monitor_position(pos, db_path)
            for exit_sig in exits:
                if exit_sig.severity == "critical":
                    closed = await self.exit_position(pos, exit_sig.rule_type, db_path)
                    if closed:
                        result.positions_closed += 1

        new_signals = await self._fetch_signals(db_path)
        candidates = self.select(new_signals, open_positions)

        for signal, side in candidates:
            pos = await self.open_position(signal, db_path, side=side)
            if pos is not None:
                self._save_position(pos, db_path)
                result.positions_opened += 1

        result.signals_generated = len(new_signals)
        return result

    def select(
        self,
        signals: list[Signal],
        open_positions: list[CarryPosition],
    ) -> list[tuple[Signal, str]]:
        """Return list of (signal, side) pairs for paired legs."""
        existing_symbols = {p.symbol.replace("USDT", "").replace("/USDT:", "") for p in open_positions}
        existing_shorts = len([p for p in open_positions if p.side == "SHORT"])
        pairs_available = min(self.max_positions // 2, self.max_positions // 2 - existing_shorts)
        if pairs_available <= 0:
            return []

        # Filter signals above threshold, sorted by APY desc
        candidates = [s for s in signals if s.apy_net >= self.min_apy
                      and s.symbol not in existing_symbols]
        candidates.sort(key=lambda s: s.apy_net, reverse=True)

        legs = []
        used_symbols = set(existing_symbols)
        for sig in candidates:
            if len(legs) >= pairs_available * 2:
                break
            sym = sig.symbol
            if sym in used_symbols:
                continue

            # SHORT leg: high funding
            legs.append((sig, "SHORT"))
            used_symbols.add(sym)

            # LONG leg: lowest APY candidate not already used
            hedge_candidates = [
                s for s in signals
                if s.symbol != sym
                and s.symbol not in used_symbols
            ]
            if hedge_candidates:
                hedge = min(hedge_candidates, key=lambda s: s.apy_net)
                legs.append((hedge, "LONG"))
                used_symbols.add(hedge.symbol)

        # Ensure even number of legs (pairs only)
        if len(legs) % 2 != 0:
            legs = legs[:-1]

        return legs

    async def open_position(
        self,
        signal: Signal,
        db_path: str,
        side: str = "SHORT",
    ) -> CarryPosition | None:
        mark_price = getattr(signal, "_mark_price", 0)
        if not mark_price or mark_price <= 0:
            from fund_rate_arb.db import get_connection

            conn = get_connection(db_path)
            try:
                row = conn.execute(
                    "SELECT mark_price FROM funding_rates "
                    "WHERE symbol = ? AND exchange = 'binance' "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (signal.symbol + "USDT",),
                ).fetchone()
                mark_price = float(row[0]) if row and row[0] else 0
            finally:
                conn.close()

        if mark_price <= 0:
            logger.warning("No mark price for %s, skipping", signal.symbol)
            return None

        pos = self.executor.open_position(signal, mark_price=mark_price, side=side)
        if pos:
            logger.info(
                "Opened %s %s: %.4f contracts @ %.2f",
                side, pos.symbol,
                pos.contracts,
                pos.entry_price,
            )
        return pos

    async def monitor_position(
        self,
        position: CarryPosition,
        db_path: str,
    ) -> list[ExitSignal]:
        symbol = position.symbol
        exchange = "binance"

        funding_48h = query_funding_window(db_path, symbol, exchange, 48)
        oi_8h = query_oi_window(db_path, symbol, exchange, 8)

        market = MarketData(
            symbol=symbol,
            exchange=exchange,
            current_mark=position.entry_price,
            current_index=position.entry_price,
            current_basis=position.entry_basis,
            funding_history_48h=funding_48h,
            oi_window_8h=oi_8h,
        )

        exits = self.exit_engine.check_all(position, market)

        oi_spike = detect_oi_spike(oi_8h)
        if oi_spike.triggered:
            exits.append(
                ExitSignal(
                    position_execution_id=position.execution_id,
                    rule_type="oi_spike",
                    severity="warning",
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
                        severity="warning",
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
        success = self.executor.close_position(position, reason)
        if success:
            position.status = "Closed"
            position.close_reason = reason
            self._update_position_status(position, db_path)
            logger.info("Closed %s: %s", position.symbol, reason)
        return success

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
        """Re-run signal detection on latest data."""
        from fund_rate_arb.db import query_all_latest
        from fund_rate_arb.signal.detector import detect_signals

        data = query_all_latest(db_path, exchange="binance")
        if not data:
            return []

        from fund_rate_arb.models.funding import FundingRate, SpreadData

        rates = []
        spreads = []
        for row in data:
            rates.append(
                FundingRate(
                    symbol=row["symbol"],
                    exchange="binance",
                    timestamp=datetime.now(timezone.utc),
                    funding_rate=row["funding_rate"],
                    mark_price=row.get("mark_price"),
                    index_price=row.get("index_price"),
                )
            )
            if row.get("spread_bps", 0) > 0:
                spreads.append(
                    SpreadData(
                        symbol=row["symbol"],
                        exchange="binance",
                        timestamp=datetime.now(timezone.utc),
                        bid=row.get("mark_price", 0) or 0,
                        ask=row.get("mark_price", 0) or 0,
                        spread_bps=row["spread_bps"],
                    )
                )

        return detect_signals(rates, spreads, apy_threshold=self.min_apy)
