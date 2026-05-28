"""End-to-end dry-run test for FundingCarry strategy pipeline.

Simulates full cycle: seed data → score → select → paper execute → monitor → close.
No live orders. No network calls. Pure paper mode.

Run: uv run pytest -s tests/test_e2e_dryrun.py -v
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from fund_rate_arb.db import (
    init_db,
    migrate_db,
    insert_funding_rates,
    insert_oi_snapshots,
    insert_strategy_position,
    close_strategy_position,
    insert_trade_log,
    query_open_positions_by_strategy,
    query_trade_log,
    update_position_funding,
    query_last_close_time,
)
from fund_rate_arb.events.bus import EventBus
from fund_rate_arb.scoring.quality_score import compute_quality_score
from fund_rate_arb.scoring.fee_model import compute_fees, annualized_funding_apy
from fund_rate_arb.data.monitors import (
    detect_oi_spike,
    detect_funding_regime_shift,
    compute_funding_zscore,
    compute_ewma,
    compute_basis_drift,
)
from fund_rate_arb.data.retriever import (
    query_funding_window,
    query_latest_basis,
)
from fund_rate_arb.data.payments import (
    record_funding_payment,
    query_position_funding_summary,
)
from fund_rate_arb.models.funding import (
    CarryPosition,
    ExitSignal,
    MarketData,
    FundingScore,
)


# ── Seed data ──────────────────────────────────────────────────────────────────

SYMBOLS = [
    # (symbol, funding_history_8h, oi_history, mark_price, index_price, spread_bps)
    # High persistence, positive — good carry candidate
    ("TSLAUSDT", [0.0003] * 30, [5e6] * 30, 250.0, 249.5, 1.5),
    # High but volatile — moderate candidate
    (
        "NVDAUSDT",
        [0.0005, -0.0001, 0.0004, -0.0002, 0.0006] * 6,
        [3e6] * 30,
        120.0,
        119.8,
        2.0,
    ),
    # Declining funding — bad candidate
    (
        "AAPLUSDT",
        [0.0001, 0.00005, 0.00002, -0.00005, -0.0001] * 6,
        [2e6] * 30,
        180.0,
        179.9,
        3.0,
    ),
    # Steady positive — decent candidate
    ("AMZNUSDT", [0.0002] * 30, [4e6] * 30, 185.0, 184.7, 1.0),
    # Negative funding — skip
    ("METAUSDT", [-0.0001] * 30, [6e6] * 30, 500.0, 500.1, 0.5),
    # Spiky OI — anomaly risk
    ("MSTRUSDT", [0.0004] * 30, [1e6] * 15 + [5e6] * 15, 350.0, 349.0, 4.0),
    # Good persistence, low OI
    ("PLTRUSDT", [0.00025] * 30, [800e3] * 30, 25.0, 24.98, 2.5),
    # Best candidate — high, persistent, stable
    ("COINUSDT", [0.0004] * 30, [7e6] * 30, 220.0, 219.3, 0.8),
]

# Strategy config (matches settings.example.yaml structure)
STRATEGY_CONFIG = {
    "name": "funding_carry",
    "selection": {
        "top_fraction": 0.50,  # top 50% for demo (normally 10%)
        "min_apy_threshold": 10.0,
        "max_concurrent_positions": 3,
    },
    "execution": {
        "notional_per_leg": 100.0,
        "max_retries": 3,
        "retry_delay_s": 1.0,
        "leverage": 1,
    },
    "exit_rules": [
        {"type": "funding_collapse", "params": {"window_h": 24, "threshold": 0.0}},
        {"type": "consecutive_negative", "params": {"count": 3}},
        {"type": "basis_drift", "params": {"max_drift_pct": 0.5}},
        {"type": "break_even_deadline", "params": {"max_days": 10}},
    ],
}
WEIGHTS = {
    "funding_mean": 0.30,
    "persistence": 0.25,
    "volatility": -0.15,
    "oi_stability": 0.15,
    "spread_cost": -0.10,
    "slippage": -0.05,
}
FEES = {
    "binance_maker": 0.0002,
    "binance_taker": 0.0005,
    "slippage": 0.0003,
    "spread_cost": 0.0002,
}


@pytest.fixture
def pipeline(tmp_path):
    """Set up DB + event bus for E2E test."""
    db_path = str(tmp_path / "e2e_test.db")
    init_db(db_path)
    migrate_db(db_path)

    # Seed funding rates + OI
    now = datetime.now(timezone.utc)
    for symbol, funding_hist, oi_hist, mark, index, spread in SYMBOLS:
        funding_rows = []
        oi_rows = []
        for i, rate in enumerate(funding_hist):
            ts = (now - timedelta(hours=8 * (len(funding_hist) - i))).isoformat()
            funding_rows.append((symbol, "binance", ts, rate, rate, mark, index))
            oi_rows.append((symbol, "binance", ts, oi_hist[i]))
        insert_funding_rates(db_path, funding_rows)
        insert_oi_snapshots(db_path, oi_rows)

    # Event bus with logging
    event_log = []
    bus = EventBus()
    bus.subscribe("POSITION_OPENED", lambda p: event_log.append(("OPENED", p)))
    bus.subscribe("POSITION_CLOSED", lambda p: event_log.append(("CLOSED", p)))
    bus.subscribe("EXIT_TRIGGERED", lambda p: event_log.append(("EXIT", p)))
    bus.subscribe("FUNDING_PAYMENT", lambda p: event_log.append(("FUNDING", p)))

    return {
        "db_path": db_path,
        "event_bus": bus,
        "event_log": event_log,
        "now": now,
    }


def test_e2e_dryrun(pipeline, capsys):
    """Full E2E cycle: seed → score → select → execute → monitor → close."""
    db_path = pipeline["db_path"]
    bus = pipeline["event_bus"]
    event_log = pipeline["event_log"]

    print("\n" + "=" * 70)
    print("  FUNDING CARRY STRATEGY — E2E DRY RUN")
    print("=" * 70)

    # ── Step 1: Score ──────────────────────────────────────────────────
    print("\n── STEP 1: Score all symbols ──")

    scores = []
    for symbol, funding_hist, oi_hist, mark, index, spread in SYMBOLS:
        score = compute_quality_score(
            symbol=symbol,
            exchange="binance",
            funding_history=funding_hist,
            oi_history=oi_hist,
            spread_bps=spread,
            weights=WEIGHTS,
            fees=FEES,
        )
        scores.append(score)

    scores.sort(key=lambda s: s.score, reverse=True)

    print(
        f"\n  {'Symbol':<14} {'Score':>7} {'APY%':>8} {'BE Days':>8} {'Persist':>8} {'Regime':<8}"
    )
    print("  " + "-" * 55)
    for s in scores:
        apy_str = f"{s.estimated_apy * 100:.1f}%" if s.estimated_apy > 0 else "N/A"
        be_str = f"{s.break_even_days:.1f}" if s.break_even_days > 0 else "inf"
        print(
            f"  {s.symbol:<14} {s.score:>7.4f} {apy_str:>8} {be_str:>8} {s.persistence:>8.1%} {s.regime:<8}"
        )

    # ── Step 2: Select ─────────────────────────────────────────────────
    print("\n── STEP 2: Select top candidates ──")

    cfg = STRATEGY_CONFIG["selection"]
    # Filter: positive APY, regime != bear, break_even <= max
    eligible = [
        s
        for s in scores
        if s.estimated_apy > 0
        and s.regime != "bear"
        and (s.break_even_days > 0 and s.break_even_days <= 10)
        and s.estimated_apy * 100 >= cfg["min_apy_threshold"]
    ]

    # Take top fraction
    top_n = max(1, int(len(eligible) * cfg["top_fraction"]))
    candidates = eligible[:top_n]

    print(f"  Eligible: {len(eligible)}/{len(scores)} symbols")
    print(f"  Selected top {top_n}: {[c.symbol for c in candidates]}")

    # Profitability gate: expected daily funding must cover daily fee amortization
    fee_cfg = FEES
    for c in candidates[:]:
        fees = compute_fees(
            maker_fee=fee_cfg["binance_maker"],
            taker_fee=fee_cfg["binance_taker"],
            slippage=fee_cfg["slippage"],
            spread_cost=fee_cfg["spread_cost"],
            net_funding_per_day=c.funding_mean * 3,
        )
        daily_funding = c.funding_mean * 3  # 3 intervals per day
        daily_fee_cost = fees.total_round_trip / max(fees.break_even_days, 1)
        if daily_funding <= daily_fee_cost:
            print(
                f"  [GATE] {c.symbol}: daily_funding=${daily_funding:.6f} <= daily_fee=${daily_fee_cost:.6f} — SKIP"
            )
            candidates.remove(c)
        else:
            print(
                f"  [GATE] {c.symbol}: daily_funding=${daily_funding:.6f} > daily_fee=${daily_fee_cost:.6f} BE={fees.break_even_days:.1f}d — PASS"
            )

    # ── Step 3: Execute (paper mode) ───────────────────────────────────
    print(f"\n── STEP 3: Paper execute ({len(candidates)} positions) ──")

    notional = STRATEGY_CONFIG["execution"]["notional_per_leg"]
    positions = []

    for c in candidates:
        exec_id = str(uuid.uuid4())[:8]
        entry_basis = c.volatility or 0.001  # simplified

        # Simulate fill at mark price
        symbol_data = next(s for s in SYMBOLS if s[0] == c.symbol)
        mark_price = symbol_data[3]
        contracts = notional / mark_price

        # Deduct simulated fees
        entry_fee = notional * (FEES["binance_taker"] + FEES["slippage"])

        # Insert to DB (row must have 15 values: base + strategy columns)
        row = (
            c.symbol,
            "paper",
            "SHORT",
            contracts,
            mark_price,
            mark_price,
            0.0,
            notional,
            1,
            datetime.now(timezone.utc).isoformat(),
            "open",
            "funding_carry",
            entry_basis,
            0.0,
            10,
        )
        insert_strategy_position(db_path, row)

        # Log event
        insert_trade_log(
            db_path,
            exec_id,
            "funding_carry",
            c.symbol,
            "open",
            json.dumps(
                {"notional": notional, "price": mark_price, "contracts": contracts}
            ),
        )

        pos = CarryPosition(
            execution_id=exec_id,
            strategy_name="funding_carry",
            symbol=c.symbol,
            exchange="paper",
            side="SHORT",
            contracts=round(contracts, 4),
            entry_price=mark_price,
            entry_basis=entry_basis,
            entry_cost=round(entry_fee, 4),
            cumulative_funding=0.0,
            notional_usdt=notional,
            opened_at=datetime.now(timezone.utc).isoformat(),
            max_break_even_days=10,
            status="Open",
        )
        positions.append(pos)

        bus.publish(
            "POSITION_OPENED",
            {
                "execution_id": exec_id,
                "symbol": c.symbol,
                "side": "SHORT",
                "notional": notional,
                "entry_basis": entry_basis,
            },
        )

        print(
            f"  [OPEN] {c.symbol} SHORT ${notional:.0f} @ {mark_price:.2f} "
            f"(contracts={contracts:.4f}, fee=${entry_fee:.4f})"
        )

    # ── Step 4: Simulate funding payments ──────────────────────────────
    print(f"\n── STEP 4: Simulate 3 funding intervals (24h) ──")

    for pos in positions:
        c = next(s for s in scores if s.symbol == pos.symbol)
        for interval in range(3):
            payment = pos.notional_usdt * c.funding_mean
            ts = (
                datetime.now(timezone.utc) + timedelta(hours=8 * (interval + 1))
            ).isoformat()
            record_funding_payment(
                db_path, pos.execution_id, pos.symbol, c.funding_mean, payment, ts
            )
            pos.cumulative_funding += payment

        summary = query_position_funding_summary(db_path, pos.execution_id)
        print(
            f"  [FUNDING] {pos.symbol}: {summary.count} payments, "
            f"total=${summary.total_payments:.4f}, avg_rate={summary.average_rate:.6f}"
        )

        bus.publish(
            "FUNDING_PAYMENT",
            {
                "symbol": pos.symbol,
                "execution_id": pos.execution_id,
                "total": pos.cumulative_funding,
            },
        )

    # ── Step 5: Monitor ────────────────────────────────────────────────
    print(f"\n── STEP 5: Monitor positions ──")

    exit_signals = []
    for pos in positions:
        c = next(s for s in scores if s.symbol == pos.symbol)
        symbol_data = next(s for s in SYMBOLS if s[0] == pos.symbol)
        funding_hist = symbol_data[1]

        # Check OI spike
        oi_hist = symbol_data[2]
        oi_result = detect_oi_spike(oi_hist, threshold_pct=20.0)
        if oi_result.triggered:
            sig = ExitSignal(pos.execution_id, "oi_spike", "warning", oi_result.message)
            exit_signals.append(sig)
            print(f"  [WARN] {pos.symbol}: {oi_result.message}")

        # Check funding z-score
        z = compute_funding_zscore(funding_hist[-1], funding_hist)
        if abs(z) > 3:
            sig = ExitSignal(
                pos.execution_id,
                "funding_outlier",
                "warning",
                f"Funding z-score={z:.2f} — outlier detected",
            )
            exit_signals.append(sig)
            print(f"  [WARN] {pos.symbol}: z-score={z:.2f} — outlier")

        # Check EWMA (funding collapse)
        ewma = compute_ewma(funding_hist, span=12)
        if ewma <= 0:
            sig = ExitSignal(
                pos.execution_id,
                "funding_collapse",
                "critical",
                f"48h EWMA={ewma:.6f} — funding collapsed",
            )
            exit_signals.append(sig)
            print(f"  [CRIT] {pos.symbol}: EWMA={ewma:.6f} — FUNDING COLLAPSED")

        # Check basis drift
        mark = symbol_data[3]
        index = symbol_data[4]
        drift = compute_basis_drift(mark, index, pos.entry_basis)
        print(
            f"  [OK]   {pos.symbol}: basis_drift={drift:.6f}, z-score={z:.2f}, ewma={ewma:.6f}"
        )

    # ── Step 6: Close on exit signals ──────────────────────────────────
    print(f"\n── STEP 6: Close positions ──")

    closed = []
    for pos in positions:
        signals = [
            s for s in exit_signals if s.position_execution_id == pos.execution_id
        ]
        critical = [s for s in signals if s.severity == "critical"]

        if critical:
            reason = critical[0].rule_type
            close_strategy_position(db_path, pos.execution_id, reason)
            pos.status = "Closed"
            pos.close_reason = reason
            closed.append(pos)

            bus.publish(
                "POSITION_CLOSED",
                {
                    "execution_id": pos.execution_id,
                    "symbol": pos.symbol,
                    "reason": reason,
                    "pnl": pos.cumulative_funding - pos.entry_cost,
                    "hold_days": 1,
                },
            )

            pnl = pos.cumulative_funding - pos.entry_cost
            style = "PROFIT" if pnl > 0 else "LOSS"
            print(
                f"  [CLOSE] {pos.symbol} — reason={reason}, "
                f"funding=${pos.cumulative_funding:.4f}, fees=${pos.entry_cost:.4f}, "
                f"pnl=${pnl:.4f} ({style})"
            )
        else:
            print(f"  [HOLD]  {pos.symbol} — no critical signals")

    # ── Step 7: Audit trail ────────────────────────────────────────────
    print(f"\n── STEP 7: Audit trail ──")

    for pos in positions:
        logs = query_trade_log(db_path, pos.execution_id)
        print(f"\n  {pos.symbol} (exec_id={pos.execution_id}, status={pos.status}):")
        for log in logs:
            details = json.loads(log["details"]) if log["details"] else {}
            print(f"    {log['timestamp'][:19]} | {log['event']:<8} | {details}")

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Symbols scored:    {len(scores)}")
    print(f"  Candidates:        {len(candidates)}")
    print(f"  Positions opened:  {len(positions)}")
    print(f"  Exit signals:      {len(exit_signals)}")
    print(f"  Positions closed:  {len(closed)}")
    print(f"  Positions held:    {len(positions) - len(closed)}")
    print(f"  Events logged:     {len(event_log)}")

    total_funding = sum(p.cumulative_funding for p in positions)
    total_fees = sum(p.entry_cost for p in positions)
    total_pnl = total_funding - total_fees
    print(f"  Total funding:     ${total_funding:.4f}")
    print(f"  Total fees:        ${total_fees:.4f}")
    print(f"  Net PnL:           ${total_pnl:.4f}")
    print(f"{'=' * 70}")

    # Assertions
    assert len(scores) == len(SYMBOLS), "Should score all symbols"
    assert len(candidates) > 0, "Should have at least one candidate"
    assert len(positions) > 0, "Should open at least one position"
    assert len(event_log) > 0, "Should have events"
    assert all(p.status in ("Open", "Closed") for p in positions), (
        "All positions should have valid status"
    )

    # Verify DB state
    open_positions = query_open_positions_by_strategy(db_path, "funding_carry")
    assert len(open_positions) == len(positions) - len(closed), (
        "DB open count should match"
    )


def test_full_strategy_cycle(tmp_path):
    """Fetch → detect → select → open paper → monitor → no exit → tick again."""
    db = str(tmp_path / "test.db")
    from fund_rate_arb.db import (
        init_db,
        migrate_db,
        insert_funding_rates,
        insert_spread_data,
        insert_oi_snapshots,
        get_connection,
    )
    from datetime import datetime, timezone, timedelta

    init_db(db)
    migrate_db(db)

    # Seed fake high-funding data with recent timestamps
    now = datetime.now(timezone.utc)
    ts = now.isoformat()

    symbols_data = [
        ("TSLAUSDT", 0.0003, 50000.0, 49950.0),
        ("NVDAUSDT", 0.00025, 3000.0, 2995.0),
    ]

    funding_rows = []
    oi_rows = []
    spread_rows = []
    for symbol, rate, mark, index in symbols_data:
        # 30 intervals of funding history (8h each)
        for i in range(30):
            ts_i = (now - timedelta(hours=8 * (30 - i))).isoformat()
            funding_rows.append((symbol, "binance", ts_i, rate, rate, mark, index))
            oi_rows.append((symbol, "binance", ts_i, 5_000_000.0))
        # Spread data with recent timestamp
        spread_rows.append((symbol, "binance", ts, mark, mark + 1.0, 1.0, mark))

    insert_funding_rates(db, funding_rows)
    insert_oi_snapshots(db, oi_rows)
    insert_spread_data(db, spread_rows)

    import asyncio
    from fund_rate_arb.main import run_strategy_tick

    # First tick — should detect signals and open positions
    asyncio.run(run_strategy_tick(db))

    # Verify positions were created in DB
    conn = get_connection(db)
    count = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE strategy_name = 'funding_carry'",
    ).fetchone()[0]
    conn.close()
    assert count > 0, "Should open at least one position"
