# Funding Rate Arbitrage — Implementation Spec v3

> **Based on:** [implementation plan v3](./funding-rate-arbitrage-implementation-v3.md)
> **Goal:** FundingCarry strategy — single-leg perp short on Binance PM, paper-first
> **Current state:** Collectors + scoring + signal detector exist. No strategy framework, no execution layer, no risk engine.

---

## Gap Analysis: What Exists vs What's Needed

### Already Exists (reuse as-is)
| File | What | Notes |
|------|------|-------|
| `collectors/binance.py` | Binance REST collector | Reuse |
| `collectors/portfolio_margin.py` | PM account + order placement | Reuse for executor |
| `collectors/hyperliquid.py` | HL collector | Phase 2 |
| `scoring/quality_score.py` | `compute_quality_score()` | Reuse |
| `scoring/persistence.py` | Persistence ratio | Reuse |
| `scoring/fee_model.py` | Fee calculation | Reuse |
| `signal/detector.py` | Signal detection (APY-based) | Partial reuse |
| `signal/scheduler.py` | `PollScheduler` | Extend with `run_strategy()` |
| `tg/sender.py` | TG message sender | Reuse |
| `models/funding.py` | `FundingRate`, `OpenInterest`, `SpreadData`, `FundingScore` | Extend |
| `db.py` | Schema + basic queries | Extend |
| `config.py` | YAML settings loader | Extend |
| `strategies/config.py` | `StrategySpec` + sub-configs | Extend |

### Needs Fix (bugs in existing code)
| File | Issue |
|------|-------|
| `strategies/__init__.py` | Imports from `fund_rate_arb.strategy.config` — path should be `strategies` not `strategy`. Fix import. |

### Needs Build (new packages/files)
| Package | Files | Purpose |
|---------|-------|---------|
| `strategies/` | `base.py`, `funding_carry.py`, `engine.py` | Strategy ABC + implementation + registry |
| `execution/` | `__init__.py`, `base.py`, `retry.py`, `allocator.py`, `paper.py`, `binance.py` | Order execution layer |
| `data/` | `__init__.py`, `retriever.py`, `monitors.py`, `payments.py` | Time-series queries + sliding window |
| `risk/` | `__init__.py`, `manager.py`, `exit_rules.py` | Pre-trade checks + exit evaluation |
| `events/` | `__init__.py`, `bus.py` | Pub/sub event bus |

---

## Phase 1: Data Foundation

No live orders. Pure data structures + DB + queries.

### 1.1 Models — `models/funding.py` (extend)

Add 4 new dataclasses after existing models:

```python
@dataclass
class CarryPosition:
    execution_id: str           # UUID
    strategy_name: str
    symbol: str
    exchange: str               # "binance_pm" | "paper"
    side: str                   # "SHORT"
    contracts: float
    entry_price: float
    entry_basis: float          # (mark - index) / index at entry
    entry_cost: float           # total fees + slippage
    cumulative_funding: float
    notional_usdt: float
    opened_at: str              # ISO timestamp
    max_break_even_days: int
    status: str                 # "Open" | "Closing" | "Closed"
    close_reason: str | None

@dataclass
class ExitSignal:
    position_execution_id: str
    rule_type: str
    severity: str               # "info" | "warning" | "critical"
    message: str

@dataclass
class MarketData:
    symbol: str
    exchange: str
    current_mark: float
    current_index: float
    current_basis: float
    funding_history_48h: list[float]
    oi_window_8h: list[float]
    distance_to_liq_pct: float | None
    predicted_funding: float | None

@dataclass
class FundingSummary:
    total_payments: float
    count: int
    average_rate: float
    last_payment_ts: str | None
```

### 1.2 DB Schema — `db.py` (extend)

Add migration function + new queries. Idempotent ALTER TABLE:

```python
def migrate_db(db_path: str) -> None:
    """Add strategy columns + trade_log table. Safe to re-run."""
    conn = get_connection(db_path)
    try:
        conn.executescript("""
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS strategy_name TEXT;
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_basis REAL DEFAULT 0;
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS cumulative_funding REAL DEFAULT 0;
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS max_break_even_days INTEGER DEFAULT 10;
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS close_reason TEXT;
            ALTER TABLE positions ADD COLUMN IF NOT EXISTS execution_id TEXT;

            ALTER TABLE trades ADD COLUMN IF NOT EXISTS strategy_name TEXT;
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS execution_id TEXT;
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS event_type TEXT;

            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY,
                execution_id TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                details TEXT,
                UNIQUE(execution_id, event, timestamp)
            );
            CREATE INDEX IF NOT EXISTS idx_trade_log_execution ON trade_log(execution_id);
            CREATE INDEX IF NOT EXISTS idx_trade_log_event ON trade_log(event);
        """)
    finally:
        conn.close()
```

New query functions to add:

| Function | Purpose |
|----------|---------|
| `query_oi_history(db, symbol, exchange, hours)` | OI snapshots in window |
| `query_funding_range(db, symbol, exchange, start_ts, end_ts)` | Funding rates in time range |
| `insert_strategy_position(db, row)` → str (UUID) | Insert with execution_id |
| `update_position_funding(db, execution_id, cumulative)` | Update cumulative_funding |
| `close_strategy_position(db, execution_id, reason)` | Set status=Closed + reason |
| `insert_trade_log(db, execution_id, strategy, symbol, event, details)` | Audit trail |
| `query_trade_log(db, execution_id)` | Full log for position |
| `query_open_positions_by_strategy(db, strategy_name)` | Open positions for strategy |
| `query_last_close_time(db, symbol, strategy)` | Last close timestamp (reentry cooldown) |

### 1.3 Events — `events/bus.py` (new)

Simple in-memory pub/sub. No persistence needed.

```python
class EventBus:
    def __init__(self): ...
    def subscribe(self, event_type: str, handler: Callable) -> None: ...
    def publish(self, event_type: str, payload: dict) -> None: ...
```

Events: `POSITION_OPENED`, `POSITION_CLOSED`, `EXIT_TRIGGERED`, `RETRY_FAILED`, `ANOMALY_DETECTED`, `COMPENSATING_CLOSE`, `FUNDING_PAYMENT`.

### 1.4 Data Layer — `data/` (new)

**`data/retriever.py`:**
- `query_funding_window(db, symbol, exchange, hours)` → list[float]
- `query_oi_window(db, symbol, exchange, hours)` → list[float]
- `query_latest_basis(db, symbol, exchange)` → float | None
- `query_cumulative_funding(db, execution_id)` → float

**`data/monitors.py`:**
- `detect_oi_spike(oi_window, threshold_pct)` → MonitorResult
- `detect_funding_regime_shift(current_window, baseline_window, stdev_multiplier)` → MonitorResult
- `compute_funding_zscore(current, window)` → float
- `compute_ewma(values, span)` → float
- `compute_basis_drift(current_mark, current_index, entry_basis)` → float

**`data/payments.py`:**
- `record_funding_payment(db, execution_id, symbol, rate, amount, timestamp)`
- `query_position_funding_summary(db, execution_id)` → FundingSummary

Payment detection: query `/papi/v1/um/income` for `incomeType=FUNDING_FEE`, match by symbol+timestamp.

### 1.5 Config — `config.py` (extend)

Add `get_strategy_specs(settings)` → list[StrategySpec] loader that reads `strategies:` section from YAML.

### Phase 1 Tests
- DB: migration idempotent, new queries return correct data
- Monitors: OI spike detection, regime shift, z-score, EWMA, basis drift, empty window
- Payments: record + query + multiple payments sum correctly
- Retriever: funding window returns correct range, empty for missing data
- Events: subscribe/publish, handler exception doesn't break others

---

## Phase 2: Risk Engine

### 2.1 RiskManager — `risk/manager.py` (new)

**Pre-trade checks:**
| Check | Condition |
|-------|-----------|
| Balance | `available_balance >= notional * 2` |
| Profitability gate | `expected_net_funding > entry_fees + exit_fees` |
| Symbol validity | In whitelist AND exchangeInfo |
| Max concurrent | `open_positions < max_concurrent` |
| Reentry cooldown | `last_close_ts > 6h ago` |
| Kill switch | `TRADING_ENABLED=true` env var |

**Per-cycle checks:**
| Check | Condition | Severity |
|-------|-----------|----------|
| Liquidation proximity | `distance_to_liq < 10%` | warning |
| Liquidation critical | `distance_to_liq < 15%` | critical |
| Concentration | >30% portfolio in one sector | warning |
| Account status | PM account != NORMAL | critical |

```python
class RiskManager:
    def __init__(self, config: RiskConfig, db_path: str): ...
    def pre_trade_check(self, symbol, notional, expected_apy, open_positions) -> RiskResult: ...
    def monitor_position(self, position, market_data) -> list[ExitSignal]: ...
    def check_liquidation_proximity(self, position) -> ExitSignal: ...
```

### 2.2 Exit Rules — `risk/exit_rules.py` (new)

12 rules, priority-ordered evaluator:

| Priority | Rule | Severity | Trigger |
|----------|------|----------|---------|
| 1 | liquidation_critical | critical | distance_to_liq < 15% |
| 2 | account_abnormal | critical | PM account != NORMAL |
| 3 | funding_flip | critical | predicted < 0 AND last settled < 0 |
| 4 | consecutive_negative | critical | N consecutive negative intervals |
| 5 | basis_drift | critical | abs(current - entry) > max_drift_pct |
| 6 | break_even_deadline | critical | max_days reached, funding < entry_cost |
| 7 | break_even_projection | warning | projected days > remaining days |
| 8 | funding_collapse | warning | 48h EMA funding < threshold |
| 9 | drawdown_realized | critical | cumulative losses > max_loss_pct |
| 10 | symbol_delisted | critical | symbol not in exchangeInfo |
| 11 | negative_carry | warning | funding income < fees per day |
| 12 | reentry_cooldown | info | last close < cooldown_h ago |

Escalation: `info` → log, `warning` → prepare exit + TG alert, `critical` → immediate market close.

### Phase 2 Tests
- Pre-trade: pass/fail for each check boundary
- Exit rules: trigger boundary, no-trigger boundary, severity, priority ordering
- Liquidation proximity: edge cases at 10% and 15%

---

## Phase 3: Execution Layer

### 3.1 Retry — `execution/retry.py` (new)

```python
async def retry_with_backoff(
    fn: Callable,
    max_retries: int = 5,
    base_delay_s: float = 2.0,
    max_delay_s: float = 30.0,
    retryable_errors: tuple[type] = (httpx.HTTPError, ccxt.NetworkError, ccxt.RateLimitExceeded),
) -> Any: ...
```

### 3.2 Allocator — `execution/allocator.py` (new)

```python
class PositionAllocator:
    def __init__(self, portfolio_usdt, max_per_position_usdt, max_portfolio_pct=80.0, max_concurrent=5): ...
    def allocate(self, candidates, open_positions) -> dict[str, float]: ...
```

Logic: `remaining = portfolio * max_portfolio_pct - already_allocated`. Split equally across available slots, cap at `max_per_position_usdt`.

### 3.3 Executor ABC — `execution/base.py` (new)

```python
class BaseExecutor(ABC):
    @property
    @abstractmethod
    def exchange_name(self) -> str: ...

    @abstractmethod
    async def open_leg(self, symbol, side, notional_usdt, order_type="market") -> OrderResult: ...

    @abstractmethod
    async def close_leg(self, symbol, side, amount) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[ExchangePosition]: ...
```

### 3.4 Paper Executor — `execution/paper.py` (new)

Shadow ledger. No real orders. Simulate fill at mid price, deduct fees.

### 3.5 Binance Executor — `execution/binance.py` (new)

Wraps `PortfolioMarginCollector`. Perp short only. Steps:
1. Fetch mark price → convert notional to contracts
2. Place market order via PM collector
3. Verify fill >= 90%
4. Record to trade_log

### Phase 3 Tests
- Retry: success on retry, max retries exhausted, non-retryable passthrough, backoff delay
- Allocator: budget allocation, max position limit, declining allocation, zero slots
- Paper: open creates position, close updates status, fees deducted
- Binance: mock exchange, order verification, contract conversion

---

## Phase 4: Strategy Framework

### 4.1 Base Strategy — `strategies/base.py` (new)

```python
class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def scan(self, config) -> list[FundingScore]: ...

    @abstractmethod
    def select(self, scores, config) -> list[FundingScore]: ...

    @abstractmethod
    async def execute(self, candidates, allocator, executor) -> list[CarryPosition]: ...

    @abstractmethod
    async def monitor(self, positions, risk_mgr, market_data) -> list[ExitSignal]: ...

    async def tick(self, config) -> None:
        """scan → select → execute → monitor → persist"""
```

### 4.2 FundingCarryStrategy — `strategies/funding_carry.py` (new)

**Scan:** Query latest funding + OI + spreads → `compute_quality_score()` → filter regime="bull", break_even_days <= max → sort by score desc.

**Select:** Rank by `estimated_apy / break_even_days` → take top 10% → profitability gate.

**Execute:** Allocate notional → open perp short → retry with backoff → record position with execution_id.

**Monitor:** Fetch open positions → run exit rules → close on critical signal → update cumulative_funding.

### 4.3 Engine — `strategies/engine.py` (new)

Registry + lifecycle manager:
```python
class StrategyEngine:
    def __init__(self, db_path, event_bus): ...
    def register_strategy(self, strategy: BaseStrategy) -> None: ...
    def get_strategy(self, name) -> BaseStrategy: ...
    def enabled_specs(self) -> list[StrategySpec]: ...
```

### 4.4 Scheduler — `signal/scheduler.py` (extend)

Add `run_strategy()` method to `PollScheduler`:
```python
async def run_strategy(self, strategy: BaseStrategy, interval: int) -> None:
    while self._running:
        try:
            await strategy.tick()
        except Exception:
            logger.exception("Strategy tick failed: %s", strategy.name)
        await asyncio.sleep(interval)
```

### 4.5 Fix — `strategies/__init__.py`

Fix import: `fund_rate_arb.strategy.config` → `fund_rate_arb.strategies.config` (and same for base).

### Phase 4 Tests
- Full cycle: scan returns scores, select filters top 10%, execute opens paper position, monitor detects exit
- Strategy engine: register/get/enabled lifecycle

---

## Phase 5: Integration

### 5.1 Telegram — `tg/formatter.py` (extend)

Add formatters: `format_exit_alert`, `format_position_opened`, `format_retry_failed`.
Alert dedup: same execution_id + rule_type within 30 min → skip.

### 5.2 CLI — `cli/main.py` (extend)

New commands:
```
fund-rate-arb strategy list
fund-rate-arb strategy run --name funding_carry
fund-rate-arb strategy run --name funding_carry --dry-run
fund-rate-arb strategy daemon --name funding_carry
fund-rate-arb strategy status
fund-rate-arb strategy close --symbol TSLA --reason manual
fund-rate-arb strategy log --execution-id <uuid>
fund-rate-arb strategy toggle --name funding_carry
```

### 5.3 Server Entry — `main.py` (extend)

Wire: init DB → EventBus → StrategyEngine → register FundingCarry → subscribe event handlers → PollScheduler → run strategies.

### Phase 5 Tests
- E2E: full cycle with paper executor (fetch data → scan → select → open → monitor → close)
- CLI: each command runs without error
- TG: formatters produce correct output

---

## Phase 6: Paper → Live

1. Run paper mode 1-2 weeks
2. Validate exit rules against real market data
3. Tune thresholds in settings.yaml
4. Switch `paper_mode: false`
5. Start small: $5-10 per position
6. Monitor closely, adjust

---

## settings.yaml Addition

```yaml
strategies:
  - name: funding_carry
    enabled: true
    polling_interval_s: 3600
    weights: {}
    selection:
      top_fraction: 0.10
      max_concurrent_positions: 5
      min_apy_threshold: 10.0
    execution:
      notional_per_leg: 200.0
      max_retries: 5
      retry_delay_s: 2.0
      leverage: 1
    exit_rules:
      - type: liquidation_critical
        params: { min_distance_pct: 15.0 }
      - type: account_abnormal
        params: {}
      - type: funding_flip
        params: {}
      - type: consecutive_negative
        params: { count: 3 }
      - type: basis_drift
        params: { max_drift_pct: 1.0 }
      - type: break_even_deadline
        params: { max_days: 10 }
      - type: funding_collapse
        params: { window_h: 48, threshold: -0.0001 }
      - type: drawdown_realized
        params: { max_loss_pct: 5.0 }
      - type: reentry_cooldown
        params: { cooldown_h: 6 }
    anomaly_detection:
      oi_window_hours: 8
      oi_change_threshold_pct: 20.0
      funding_stdev_lookback_days: 30
      funding_stdev_threshold: 2.0
```

---

## File Inventory (all new/modified files)

| # | File | Action | Phase |
|---|------|--------|-------|
| 1 | `src/fund_rate_arb/models/funding.py` | Extend: +4 dataclasses | 1 |
| 2 | `src/fund_rate_arb/db.py` | Extend: migrate + 9 queries | 1 |
| 3 | `src/fund_rate_arb/events/__init__.py` | New | 1 |
| 4 | `src/fund_rate_arb/events/bus.py` | New: EventBus | 1 |
| 5 | `src/fund_rate_arb/data/__init__.py` | New | 1 |
| 6 | `src/fund_rate_arb/data/retriever.py` | New: 4 query functions | 1 |
| 7 | `src/fund_rate_arb/data/monitors.py` | New: 5 detector functions | 1 |
| 8 | `src/fund_rate_arb/data/payments.py` | New: payment tracker | 1 |
| 9 | `src/fund_rate_arb/config.py` | Extend: strategy spec loader | 1 |
| 10 | `src/fund_rate_arb/risk/__init__.py` | New | 2 |
| 11 | `src/fund_rate_arb/risk/manager.py` | New: RiskManager | 2 |
| 12 | `src/fund_rate_arb/risk/exit_rules.py` | New: ExitRuleEngine + 12 rules | 2 |
| 13 | `src/fund_rate_arb/execution/__init__.py` | New | 3 |
| 14 | `src/fund_rate_arb/execution/base.py` | New: BaseExecutor ABC | 3 |
| 15 | `src/fund_rate_arb/execution/retry.py` | New: retry_with_backoff | 3 |
| 16 | `src/fund_rate_arb/execution/allocator.py` | New: PositionAllocator | 3 |
| 17 | `src/fund_rate_arb/execution/paper.py` | New: PaperExecutor | 3 |
| 18 | `src/fund_rate_arb/execution/binance.py` | New: BinanceSingleLegExecutor | 3 |
| 19 | `src/fund_rate_arb/strategies/base.py` | New: BaseStrategy ABC | 4 |
| 20 | `src/fund_rate_arb/strategies/funding_carry.py` | New: FundingCarryStrategy | 4 |
| 21 | `src/fund_rate_arb/strategies/engine.py` | New: StrategyEngine | 4 |
| 22 | `src/fund_rate_arb/strategies/__init__.py` | Fix: import paths | 4 |
| 23 | `src/fund_rate_arb/signal/scheduler.py` | Extend: run_strategy() | 4 |
| 24 | `src/fund_rate_arb/tg/formatter.py` | Extend: +3 formatters | 5 |
| 25 | `src/fund_rate_arb/cli/main.py` | Extend: +8 commands | 5 |
| 26 | `src/fund_rate_arb/main.py` | Extend: daemon entry | 5 |
| 27 | `tests/` | New: per-phase test files | 1-5 |

**Total: 14 new files, 7 modified files, 6 new test files.**
