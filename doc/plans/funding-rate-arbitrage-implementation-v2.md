# Funding Rate Arbitrage — Implementation Plan v3

> **Revision:** Incorporates critical architecture review + 4 open-source repo analysis
> **Strategy:** Multi-strategy framework, FundingCarry first
> **Exchange:** Binance PM (Phase 1), Hyperliquid (Phase 2)

---

## Architecture Overview

```
                     ┌──────────────────────────────────────┐
                     │    CLI / Server Entry (main.py)       │
                     ├──────────────────────────────────────┤
                     │  Strategy Engine                      │
                     │  ┌────────────────────────────────┐   │
                     │  │ FundingCarryStrategy           │   │
                     │  │  scan → select → execute       │   │
                     │  │  → monitor → persist → tick    │   │
                     │  └────────────────────────────────┘   │
                     ├──────────────────────────────────────┤
   New packages →    │ strategies/  execution/  data/  risk/ │
                     ├──────────────────────────────────────┤
   Existing →        │ Scoring   Signal   collectors/  models│
   (reused)          │ db.py     config.py  tg/              │
                     ├──────────────────────────────────────┤
   New →             │ Event bus (simple pub/sub)            │
                     │ Trade log table (correlated legs)     │
                     │ Funding payment tracker               │
                     ──────────────────────────────────────┘
```

**Key architectural decisions (from repo analysis):**

| Decision | Why | Source |
|----------|-----|--------|
| Single-leg execution (Binance) | Stock perps don't exist on Binance spot. No spot leg. | Research |
| Trade log with `strategy_execution_id` | Correlate open/close events per position. Attribute PnL. | 50shadesofgwei |
| `deco_retry` decorator on every API call | Transient failures WILL happen live. | 50shadesofgwei |
| Triple-barrier exit (TP/SL/time) | Most robust exit model found in production code. | Hummingbot |
| Funding payment tracking | Expected vs realized PnL differs. Must track actual payments. | Hummingbot |
| Event bus (pub/sub) | Decouples strategies, execution, risk, alerts. | 50shadesofgwei |
| Liquidation proximity check | Binance PM uses mark price. Must monitor distance. | 50shadesofgwei |
| Profitability gate before entry | Don't enter if fees > expected funding profit. | Hummingbot |
| No new DB tables | Extend existing `positions` and `trades`. Avoid duplication. | Review |
| Extend `PollScheduler` | Don't create duplicate scheduler. | Review |

**Package responsibilities:**

| Package | Purpose | Status |
|---------|---------|--------|
| `strategies/` | BaseStrategy ABC, FundingCarryStrategy, strategy config models | New |
| `execution/` | Executor ABC, retry_with_backoff, position allocator, dual-leg compensator | New |
| `data/` | Time-series retriever, sliding window monitors, EMA/z-score | New |
| `risk/` | Unified RiskManager, ExitRuleEngine, liquidation monitor | New |
| `events/` | Simple pub/sub event bus | New |
| `collectors/binance_spot.py` | Spot collector — **REMOVED** (stock perps don't exist on spot) | Scrapped |
| `scoring/` | persistence, fee_model, quality_score | Reuse |
| `collectors/` | binance, hyperliquid, portfolio_margin, base | Reuse |
| `signal/` | detector (alerts), scheduler (extended) | Reuse + extend |
| `tg/` | Telegram sender | Reuse |
| `models/` | funding.py + new CarryPosition, ExitSignal, TradeEvent | Extend |
| `db.py` | Existing tables + new columns + trade_log + queries | Extend |
| `config.py` | Settings loader, Underlying, StrategySpec | Extend |

---

## Core Corrections from v2

### 1. FundingCarry = Single-Leg on Binance

**v2 mistake:** Assumed dual-leg (spot long + perp short) on Binance.

**Reality:** Binance stock perps exist ONLY on `fapi.binance.com`. No `TSLA/USDT` spot pair exists. The "carry" comes from shorting the perp and collecting funding. No spot leg needed.

**Fix:** `BinanceDualLegExecutor` → `BinanceSingleLegExecutor`. Executes perp short only. Dual-leg executor exists only for cross-exchange scenarios (e.g., long spot on CEX A, short perp on CEX B) in Phase 2.

### 2. Basis Uses Mark vs Index (Not Spot)

**v2 mistake:** Basis = perp_mark - spot_mid.

**Reality:** Since no spot leg exists, basis = `mark_price - index_price` from `/fapi/v1/premiumIndex`. Index price IS Binance's composite spot reference.

**Fix:** `data/monitors.py` `compute_basis_drift()` uses mark and index prices from FundingRate model.

### 3. DB: Extend, Don't Duplicate

**v2 mistake:** New `strategy_positions` and `strategy_events` tables.

**Fix:** Add columns to existing tables:

```sql
-- positions table additions
ALTER TABLE positions ADD COLUMN strategy_name TEXT;
ALTER TABLE positions ADD COLUMN entry_basis REAL DEFAULT 0;
ALTER TABLE positions ADD COLUMN cumulative_funding REAL DEFAULT 0;
ALTER TABLE positions ADD COLUMN max_break_even_days INTEGER DEFAULT 10;
ALTER TABLE positions ADD COLUMN close_reason TEXT;
ALTER TABLE positions ADD COLUMN execution_id TEXT;  -- correlates open/close

-- trades table additions
ALTER TABLE trades ADD COLUMN strategy_name TEXT;
ALTER TABLE trades ADD COLUMN execution_id TEXT;  -- links to position
ALTER TABLE trades ADD COLUMN event_type TEXT;    -- 'open' | 'close' | 'funding_payment' | 'retry_failed'

-- NEW: trade_log table (from 50shadesofgwei pattern)
CREATE TABLE trade_log (
    id INTEGER PRIMARY KEY,
    execution_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event TEXT NOT NULL,           -- 'open', 'close', 'funding', 'retry', 'alert'
    timestamp TEXT NOT NULL,
    details TEXT,                   -- JSON payload
    UNIQUE(execution_id, event, timestamp)
);
```

### 4. Scheduler: Extend, Don't Duplicate

**v2 mistake:** New `StrategyEngine` separate from `PollScheduler`.

**Fix:** Extend `PollScheduler` to accept strategy callbacks:

```python
class PollScheduler:
    async def run_strategy(self, strategy: BaseStrategy, interval: int) -> None:
        """Run strategy.scan() at interval, then execute/monitor."""
```

### 5. Exit Rules: Configurable, Not Hardcoded

**v2 mistake:** Exit thresholds hardcoded (e.g., "0.002%").

**Fix:** All thresholds defined in `settings.yaml` per strategy. `ExitRule` config model:

```yaml
strategies:
  - name: funding_carry
    exit_rules:
      - type: funding_collapse
        window_h: 48
        threshold: -0.0001        # negative funding = exit
      - type: consecutive_negative
        count: 3
      - type: basis_drift
        max_drift_pct: 1.0        # abs(current - entry) > 1%
      - type: break_even_deadline
        max_days: 10
      - type: drawdown
        max_loss_pct: 5.0         # realized losses only
      - type: liquidation_proximity
        min_distance_pct: 10.0    # warn if <10% from liq price
```

---

## 1. Strategy Framework (`strategies/`)

### 1.1 Config Models (`strategies/config.py`)

Pydantic models loaded from `settings.yaml` `strategies:` section:

```python
@dataclass
class StrategySpec:
    name: str
    enabled: bool
    scan_interval_s: int           # how often to scan (default 3600 = 1hr)
    weights: dict[str, float]      # override global scoring weights
    fees: dict[str, float]         # override global fees
    selection: SelectionConfig
    execution: ExecutionConfig
    exit_rules: list[ExitRule]
    anomaly: AnomalyConfig
```

### 1.2 Base Strategy (`strategies/base.py`)

```python
class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def scan(self, config: StrategySpec) -> list[FundingScore]:
        """Query data, score candidates, return sorted list."""

    @abstractmethod
    def select(self, scores: list[FundingScore], config: StrategySpec) -> list[FundingScore]:
        """Filter + rank to allocation candidates."""

    @abstractmethod
    async def execute(self, candidates: list[FundingScore], allocator: PositionAllocator) -> list[CarryPosition]:
        """Open positions. Handle retry, compensating transactions."""

    @abstractmethod
    async def monitor(self, positions: list[CarryPosition], engine: RiskManager) -> list[ExitSignal]:
        """Check risk conditions. Return exit signals."""

    async def tick(self, config: StrategySpec) -> None:
        """Full cycle: scan → select → execute → monitor → persist."""
```

### 1.3 FundingCarryStrategy (`strategies/funding_carry.py`)

**Scan:** Query perp funding rates from Binance/Hyperliquid collectors → compute quality scores → filter by regime="bull" and break_even_days <= max.

**Select:** Rank by `estimated_apy / break_even_days` (return per day to breakeven). Take top 10%.

**Execute:** For each candidate:
1. Allocate notional via PositionAllocator
2. Open perp short via BinanceSingleLegExecutor (retry 5x with exponential backoff)
3. On fill: record CarryPosition with entry_basis (mark-index), entry_cost, execution_id
4. On failure after retries: send TG alert, skip

**Monitor:** Run all exit rules via RiskManager. On critical signal → close position, log event, send alert.

**Persist:** After each cycle, persist funding rates and OI to DB. Update cumulative_funding for open positions.

---

## 2. Execution Layer (`execution/`)

### 2.1 Executor (`execution/base.py`)

```python
class BaseExecutor(ABC):
    @property
    @abstractmethod
    def exchange_name(self) -> str: ...

    @abstractmethod
    async def open_leg(self, symbol: str, side: str, notional_usdt: float,
                       order_type: str = "market") -> OrderResult: ...

    @abstractmethod
    async def close_leg(self, symbol: str, side: str, amount: float) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[ExchangePosition]: ...
```

### 2.2 Binance Single-Leg Executor (`execution/binance.py`)

Wraps `PortfolioMarginCollector`. For FundingCarry: opens perp short only.

```python
class BinanceSingleLegExecutor(BaseExecutor):
    async def open_leg(self, symbol, side, notional_usdt, order_type="market"):
        # 1. Fetch current price for contract conversion
        # 2. notional_to_contracts(notional, price, contract_size)
        # 3. Place market order via PortfolioMarginCollector.place_order()
        # 4. Verify fill
        # 5. Record to trade_log
```

**Dual-leg executor (Phase 2):** For cross-exchange scenarios with compensating transactions:

```python
class DualLegExecutor(BaseExecutor):
    async def open_dual_leg(self, long_leg, short_leg):
        # 1. Open long leg
        # 2. If long succeeds but short fails after retries:
        #    → immediately market-close long leg (compensating transaction)
        #    → log execution_id, event='compensating_close'
        # 3. If both succeed: log both legs with same execution_id
```

### 2.3 Retry (`execution/retry.py`)

Based on 50shadesofgwei `deco_retry` pattern:

```python
async def retry_with_backoff(
    fn: Callable,
    max_retries: int = 5,
    base_delay_s: float = 2.0,
    max_delay_s: float = 30.0,
    retryable_errors: tuple[type] = (httpx.HTTPError, ccxt.NetworkError, ccxt.RateLimitExceeded),
) -> Any:
    """Exponential backoff. Logs each retry attempt."""
```

### 2.4 Position Allocator (`execution/allocator.py`)

```python
class PositionAllocator:
    def allocate(self, candidates: list[FundingScore], portfolio_usdt: float,
                 max_per_position_usdt: float, max_positions: int) -> dict[str, float]:
        """Returns {symbol: notional_usdt} mapping."""
```

Logic:
- `remaining_budget = portfolio_usdt * 0.80` (max 80% allocation)
- `per_slot = remaining_budget / len(candidates)`
- `actual = min(per_slot, max_per_position_usdt)`
- Deduct from remaining as positions are allocated

---

## 3. Data Layer (`data/`)

### 3.1 Time-Series Retriever (`data/retriever.py`)

```python
def query_funding_window(db_path, symbol, exchange, hours: int) -> list[float]:
    """Return funding rates in the last N hours, oldest first."""

def query_oi_window(db_path, symbol, exchange, hours: int) -> list[float]:
    """Return OI snapshots in the last N hours."""

def query_latest_basis(db_path, symbol, exchange: str = "binance") -> float | None:
    """Return current basis (mark - index) from funding_rates table."""

def query_cumulative_funding(db_path, execution_id: str) -> float:
    """Sum of all funding_payment events for this position."""
```

### 3.2 Sliding Window Monitors (`data/monitors.py`)

Anomaly detectors for strategy monitoring:

```python
def detect_oi_spike(oi_window: list[float], threshold_pct: float = 20.0) -> MonitorResult:
    """|oi_now - oi_8h_ago| / oi_8h_ago > threshold."""

def detect_funding_regime_shift(current_window: list[float], baseline_window: list[float],
                                 stdev_multiplier: float = 2.0) -> MonitorResult:
    """std(current) / std(baseline) > multiplier → regime shift."""

def compute_funding_zscore(current: float, window: list[float]) -> float:
    """z = (current - mean) / std. |z| > 3 = outlier."""

def compute_ewma(values: list[float], span: int = 12) -> float:
    """Exponential weighted moving average for funding rate smoothing."""

def compute_basis_drift(current_mark: float, current_index: float,
                        entry_basis: float) -> float:
    """abs((mark - index) / index - entry_basis)."""
```

### 3.3 Funding Payment Tracker (`data/payments.py`)

**From Hummingbot:** Track actual funding payments, not just expected rates.

```python
def record_funding_payment(db_path, execution_id: str, symbol: str,
                           rate: float, amount: float, timestamp: str) -> None:
    """Record actual funding payment received for a position."""

def query_position_funding_summary(db_path, execution_id: str) -> FundingSummary:
    """Sum of payments, count, average rate, last payment time."""
```

---

## 4. Risk Engine (`risk/`)

### 4.1 RiskManager (`risk/manager.py`)

Replaces `trading/engine.py:RiskManager`. Two layers:

**Pre-trade checks** (before entering):
- Balance check (>= 2x min_notional)
- Profitability gate: expected net funding > entry+exit fees
- Symbol validity: still in whitelist, still trading
- Max concurrent positions not exceeded
- Re-entry cooldown: no entry within 6h of last close on this symbol

**Per-cycle checks** (monitoring open positions):
- Liquidation proximity: `distance_to_liq < min_distance_pct` → warning
- Spot liquidity (for dual-leg): order book depth < 2x notional → warning
- Concentration: >30% portfolio in one sector → warning

```python
class RiskManager:
    def pre_trade_check(self, symbol: str, notional: float, expected_apy: float) -> RiskResult: ...
    def monitor_position(self, position: CarryPosition, market_data: MarketData) -> list[ExitSignal]: ...
    def check_liquidation_proximity(self, position: CarryPosition) -> ExitSignal: ...
```

### 4.2 Exit Rules (`risk/exit_rules.py`)

From Hummingbot triple-barrier + 50shadesofgwei liquidation check:

| Rule | Trigger | Severity | Source |
|------|---------|----------|--------|
| funding_collapse | 48h EMA funding < threshold | warning | v2 |
| consecutive_negative | 3 consecutive negative intervals | critical | v2 |
| basis_drift | abs(current_basis - entry_basis) > max_drift_pct | critical | Review |
| break_even_deadline | max_days reached, funding < entry_cost | critical | v2 |
| break_even_projection | projected days > remaining | warning | Review |
| drawdown_realized | cumulative realized losses > max_loss_pct | critical | Hummingbot |
| symbol_delisted | symbol not in exchange exchangeInfo | critical | Review |
| negative_carry | funding income < fees per day | warning | Review |
| reentry_cooldown | last close < 6h ago | info | v2 |

```python
class ExitRuleEngine:
    def evaluate_all(self, position: CarryPosition, market_data: MarketData,
                     rules: list[ExitRule]) -> list[ExitSignal]:
        """Run all rules, return sorted by severity."""
```

**Severity escalation:** `info` → log only, `warning` → prepare exit + TG alert, `critical` → immediate market close.

---

## 5. Event Bus (`events/`)

**From 50shadesofgwei `EventsDirectory` pattern.** Simple in-memory pub/sub:

```python
class EventBus:
    def subscribe(self, event_type: str, handler: Callable) -> None: ...
    def publish(self, event_type: str, payload: dict) -> None: ...
```

Events:
- `POSITION_OPENED` → update positions table, send TG confirmation
- `POSITION_CLOSED` → update positions table, log PnL, send TG
- `SIGNAL_DETECTED` → log scan result
- `EXIT_TRIGGERED` → send TG alert with rule details
- `RETRY_FAILED` → send TG alert
- `ANOMALY_DETECTED` → log OI spike, funding shift
- `COMPENSATING_CLOSE` → log when one leg fails and other is closed

---

## 6. Database (`db.py`)

### 6.1 Schema Additions

See "DB: Extend, Don't Duplicate" section above. All via `ALTER TABLE` and one new `trade_log` table.

### 6.2 New Query Functions

```python
def query_oi_history(db_path, symbol, exchange, hours: int) -> list[dict]: ...
def query_funding_range(db_path, symbol, exchange, start_ts, end_ts) -> list[dict]: ...
def insert_strategy_position(db_path, row) -> str:  # returns execution_id
def update_position_funding(db_path, execution_id, cumulative) -> None: ...
def close_strategy_position(db_path, execution_id, reason) -> None: ...
def insert_trade_log(db_path, execution_id, strategy, symbol, event, details) -> None: ...
def query_trade_log(db_path, execution_id) -> list[dict]: ...
```

---

## 7. Config (`settings.yaml`)

New `strategies:` section:

```yaml
strategies:
  - name: funding_carry
    enabled: true
    scan_interval_s: 3600          # hourly scan
    selection:
      top_pct: 10                  # top 10% of scored candidates
      min_apy: 10.0                # minimum APY% to consider
      max_break_even_days: 10
    execution:
      max_concurrent: 5
      max_per_position_usdt: 200
      max_portfolio_pct: 80
      retry_max: 5
      retry_base_delay_s: 2.0
    exit_rules:
      - type: funding_collapse
        window_h: 48
        threshold: -0.0001
      - type: consecutive_negative
        count: 3
      - type: basis_drift
        max_drift_pct: 1.0
      - type: break_even_deadline
        max_days: 10
      - type: drawdown
        max_loss_pct: 5.0
      - type: liquidation_proximity
        min_distance_pct: 10.0
    anomaly:
      oi_spike_window_h: 8
      oi_spike_threshold_pct: 20
      funding_shift_stdev_mult: 2.0
      funding_shift_baseline_days: 7
```

---

## 8. CLI Additions (`cli/main.py`)

```
fund-rate-arb strategy list              # Show enabled strategies
fund-rate-arb strategy run --name funding_carry --dry-run  # Run one cycle
fund-rate-arb strategy daemon --name funding_carry         # Continuous loop
fund-rate-arb strategy status                            # Open positions, PnL, basis
fund-rate-arb strategy close --symbol TSLA --reason manual  # Force exit
fund-rate-arb strategy log --execution-id <id>           # Trade log for a position
```

---

## 9. Server Entry (`main.py`)

Updated to start `StrategyEngine` alongside existing signal scanner:

```python
async def main():
    engine = StrategyEngine(db_path)
    engine.register_strategy(FundingCarryStrategy())
    # Start scheduler for each strategy
    for spec in engine.enabled_specs():
        scheduler.run_strategy(engine.get_strategy(spec.name), spec.scan_interval_s)
```

---

## Implementation Order

### Phase 1: Data Foundation (no strategy logic)

1. **DB schema migration** — `ALTER TABLE` columns + `trade_log` table + new query functions
2. **`models/funding.py` additions** — `CarryPosition`, `ExitSignal`, `TradeEvent`, `FundingSummary` dataclasses
3. **`events/` package** — Simple EventBus pub/sub
4. **`config.py` update** — `StrategySpec` model, `get_strategy_specs()` loader

### Phase 2: Risk & Monitoring

5. **`data/retriever.py`** — Time-series query functions
6. **`data/monitors.py`** — Sliding window detectors (OI spike, regime shift, z-score, EWMA, basis drift)
7. **`data/payments.py`** — Funding payment tracker (from Hummingbot pattern)
8. **`risk/manager.py`** — RiskManager with pre-trade checks + liquidation proximity
9. **`risk/exit_rules.py`** — ExitRuleEngine with all 9 rules
10. **Tests** — Boundary tests for each risk check, timing edge cases

### Phase 3: Execution Layer

11. **`execution/retry.py`** — `retry_with_backoff()` decorator (from 50shadesofgwei)
12. **`execution/allocator.py`** — PositionAllocator
13. **`execution/base.py`** — Executor ABC
14. **`execution/binance.py`** — BinanceSingleLegExecutor wrapping PortfolioMarginCollector
15. **Tests** — Mock exchange, test retry logic, partial fills, allocator logic

### Phase 4: Strategy Framework

16. **`strategies/config.py`** — StrategySpec Pydantic model
17. **`strategies/base.py`** — BaseStrategy ABC
18. **`strategies/funding_carry.py`** — Full implementation
19. **`strategies/engine.py`** — StrategyEngine (registry + lifecycle)
20. **Extend `signal/scheduler.py`** — `run_strategy()` method

### Phase 5: Integration

21. **`cli/main.py`** — New strategy commands
22. **`main.py`** — Strategy daemon entry point
23. **`tg/` integration** — Map exit rules to TG message templates, alert dedup
24. **End-to-end tests** — Full cycle with mocked executors
25. **`trading/engine.py`** — Mark deprecated, keep backward compat for existing CLI commands

---

## Test Plan

| Package | Tests |
|---------|-------|
| `execution/retry.py` | Success on retry, max retries exhausted, non-retryable error passes through, backoff delay increases |
| `execution/allocator.py` | Budget allocation, max position limit, declining allocation |
| `execution/binance.py` | Open leg success, open leg failure after retries, contract conversion |
| `data/monitors.py` | OI spike detection, regime shift, z-score, EWMA, basis drift, empty window handling |
| `data/payments.py` | Record payment, query summary, multiple payments sum |
| `risk/manager.py` | Pre-trade pass/fail, liquidation proximity, balance check |
| `risk/exit_rules.py` | Each rule: trigger boundary, no-trigger boundary, severity assignment |
| `events/` | Subscribe/publish, multiple handlers, event filtering |
| `strategies/funding_carry.py` | Full cycle with mocked data: scan returns scores, select filters top 10%, execute opens position, monitor detects exit |

---

## Known Limitations (Deferred)

| Limitation | Deferral reason |
|-----------|-----------------|
| Dual-leg executor with atomic compensation | Requires Phase 2 cross-exchange support |
| Backtesting module | No historical data yet. Build after Phase 1 data collection runs for 2+ weeks |
| WebSocket data ingestion | REST polling sufficient for hourly scan. WS adds complexity for Phase 1 |
| Config hot-reload | File watcher adds complexity. Manual restart acceptable for now |
| Multi-exchange basis normalization | Phase 2 when Hyperliquid execution is added |
