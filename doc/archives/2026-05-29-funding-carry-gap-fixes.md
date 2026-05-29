# Funding Carry Strategy Gap Fixes

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix critical gaps in the funding rate carry strategy to make it operational and enforce strict delta-neutral risk control.

**Architecture:** The strategy SHORTs perpetual futures on `fapi.binance.com` and LONGs spot via Binance Alpha Ondo tokens (`{TICKER}on`) through the Binance Convert API (`/sapi/v1/convert/*`). 

**Key Insight:** Spot symbols are deterministic from perp symbols:
- Equity perps (`TSLAUSDT`) → spot is `{ticker}on` (`TSLAon`) via Convert API
- Crypto perps (`BTCUSDT`) → spot is `{ticker}USDT` (`BTCUSDT`) via standard spot API
- No need to store `binance_s` in YAML — auto-derive from `binance_f` and `sector`

**Tech Stack:** Python 3.11+, pytest, SQLite, ccxt, Binance Convert REST API

---

## Spot Symbol Mapping (Auto-Derived)

| Ticker | Perp (`binance_f`) | Spot (auto-derived) | Spot API | Sector |
|--------|-------------------|---------------------|----------|--------|
| BTC | BTCUSDT | BTCUSDT | Standard Spot | crypto_perp |
| TSLA | TSLAUSDT | TSLAon | Convert | equity |
| NVDA | NVDAUSDT | NVDAon | Convert | equity |
| AAPL | AAPLUSDT | AAPLon | Convert | equity |
| MSFT | MSFTUSDT | MSFTon | Convert | equity |
| AMZN | AMZNUSDT | AMZNon | Convert | equity |
| META | METAUSDT | METAon | Convert | equity |
| GOOGL | GOOGLUSDT | GOOGLon | Convert | equity |
| PLTR | PLTRUSDT | PLTRon | Convert | equity |
| MSTR | MSTRUSDT | MSTRon | Convert | equity |
| COIN | COINUSDT | COINon | Convert | equity |
| AMD | AMDUSDT | AMDon | Convert | equity |
| HOOD | HOODUSDT | HOODon | Convert | equity |
| ORCL | ORCLUSDT | ORCLon | Convert | equity |
| INTC | INTCUSDT | INTCon | Convert | equity |
| BABA | BABAUSDT | BABAon | Convert | equity |
| MU | MUUSDT | MUon | Convert | equity |
| CRCL | CRCLUSDT | CRCLon | Convert | equity |
| JPM | JPMUSDT | JPMon | Convert | equity |
| TSM | TSMUSDT | TSMon | Convert | equity |
| ARM | ARMUSDT | ARMon | Convert | equity |
| QCOM | QCOMUSDT | QCOMon | Convert | equity |

**Binance Convert API:**
- `GET /sapi/v1/convert/exchangeInfo?fromAsset=USDT&toAsset=TSLAon` — verify pair availability
- `POST /sapi/v1/convert/getQuote` — signed, returns `inverseRatio` = price in USDT
- Quote valid ~15 seconds; use for price discovery + execution

---

## Task 1: Simplify Underlying Model — Auto-Derive Spot Symbols

**Context:** Currently `Underlying` stores `binance_s` explicitly, but it's always `null` for equities and `BTCUSDT` for BTC. Since the Convert API convention is `{ticker}on` for equities, we can auto-derive this from `binance_f` and `sector`.

**Files:**
- Modify: `src/fund_rate_arb/config.py:107-131`
- Modify: `settings.example.yaml` (remove all `binance_s` fields)
- Modify: `settings.yaml` (live, gitignored — same changes)

**Step 1: Update `Underlying` dataclass in `config.py`**

Remove `binance_s` field, add `binance_spot` property:

```python
@dataclass(frozen=True)
class Underlying:
    ticker: str
    name: str
    binance_f: str | None
    hl_perp: str | None
    hl_spot: str | None
    sector: str

    @property
    def binance_spot(self) -> str | None:
        """Auto-derive spot symbol: equity→{ticker}on (Convert), crypto→{ticker}USDT."""
        if self.binance_f is None:
            return None
        if self.sector == "equity":
            return f"{self.ticker}on"
        if self.sector in ("crypto_perp", "crypto"):
            return f"{self.ticker}USDT"
        return None
```

**Step 2: Update `_parse_underlyings()` to remove `binance_s` parsing**

```python
def _parse_underlyings(raw: list[dict]) -> list[Underlying]:
    """Convert YAML list to Underlying objects."""
    results = []
    for entry in raw:
        results.append(Underlying(
            ticker=entry["ticker"],
            name=entry.get("name", entry["ticker"]),
            binance_f=entry.get("binance_f"),
            hl_perp=entry.get("hl_perp"),
            hl_spot=entry.get("hl_spot"),
            sector=entry.get("sector", "equity"),
        ))
    return results
```

**Step 3: Update `WHITELIST_BINANCE_SPOT` derivation**

```python
WHITELIST_BINANCE_SPOT: set[str] = {
    u.binance_spot for u in UNDERLYINGS if u.binance_spot is not None and _is_tradable_sector(u.sector)
}
```

**Step 4: Remove `binance_s` from `settings.example.yaml`**

Delete all `binance_s: null` and `binance_s: BTCUSDT` lines (27 occurrences).

**Step 5: Run tests**

```bash
uv run pytest tests/ -v -k "config or underlying"
```

**Step 6: Commit**

```bash
git add src/fund_rate_arb/config.py settings.example.yaml
git commit -m "refactor: auto-derive binance_spot from binance_f + sector"
```

---

## Task 2: Build Binance Convert API Spot Collector

**Context:** `BinanceSpotCollector` in `collectors/binance_spot.py` currently uses ccxt standard spot API which doesn't have Ondo tokens. Must use Binance Convert API (`/sapi/v1/convert/*`) for price discovery and execution.

**Files:**
- Modify: `src/fund_rate_arb/collectors/binance_spot.py`
- Test: `tests/collectors/test_binance_spot.py`

**Step 1: Write failing test for Convert API price fetch**

```python
def test_convert_collector_fetches_price_via_getquote():
    """BinanceSpotCollector should use /sapi/v1/convert/getQuote for Ondo tokens."""
    collector = BinanceSpotCollector()
    price = collector.fetch_convert_price(from_asset="USDT", to_asset="TSLAon")
    assert price > 0
```

**Step 2: Implement Convert API methods**

Replace `BinanceSpotCollector` with Convert API implementation:

```python
class BinanceSpotCollector(BaseCollector):
    """Binance spot via Convert API for Ondo tokenized stocks."""

    SAPI_BASE = "https://api.binance.com"

    def __init__(self) -> None:
        api_key = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_SECRET", "")
        if not api_key or not secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_SECRET must be set")
        self._api_key = api_key
        self._secret = secret

    def _sign(self, params: dict) -> str:
        import hmac, hashlib
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        return hmac.new(self._secret.encode(), qs.encode(), hashlib.sha256).hexdigest()

    def fetch_convert_price(self, from_asset: str, to_asset: str, amount: float = 100.0) -> float:
        """Get live price via Convert API. Returns price of to_asset in from_asset."""
        import time, httpx
        timestamp = int(time.time() * 1000)
        params = {
            "fromAsset": from_asset,
            "toAsset": to_asset,
            "fromAmount": str(amount),
            "timestamp": str(timestamp),
        }
        params["signature"] = self._sign(params)
        headers = {"X-MBX-APIKEY": self._api_key}
        proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
        proxies = {"http://": proxy, "https://": proxy} if proxy else None
        with httpx.Client(proxies=proxies) as client:
            resp = client.post(
                f"{self.SAPI_BASE}/sapi/v1/convert/getQuote",
                params=params, headers=headers, timeout=10,
            )
        data = resp.json()
        return float(data["inverseRatio"]) if "inverseRatio" in data else 0.0

    def fetch_convert_exchange_info(self, from_asset: str, to_asset: str) -> dict:
        """Verify pair availability and get size limits."""
        import httpx
        proxy = os.environ.get("BINANCE_PROXY", BINANCE_PROXY)
        proxies = {"http://": proxy, "https://": proxy} if proxy else None
        with httpx.Client(proxies=proxies) as client:
            resp = client.get(
                f"{self.SAPI_BASE}/sapi/v1/convert/exchangeInfo",
                params={"fromAsset": from_asset, "toAsset": to_asset},
                timeout=10,
            )
        return resp.json()

    def place_order(self, symbol: str, side: str, amount: float, **kwargs) -> OrderResult:
        """Execute via Convert API acceptQuote after getQuote."""
        # symbol is the Ondo token name (e.g., "TSLAon")
        from_asset = "USDT" if side == "buy" else symbol
        to_asset = symbol if side == "buy" else "USDT"
        # ... getQuote then acceptQuote flow
```

**Step 3: Implement `fetch_spreads()` for Convert tokens**

Since Convert API doesn't have an order book, approximate spread from the Convert quote ratio vs the perp mark price.

**Step 4: Run tests**

```bash
uv run pytest tests/collectors/ -v
```

**Step 5: Commit**

```bash
git add src/fund_rate_arb/collectors/binance_spot.py tests/collectors/test_binance_spot.py
git commit -m "feat: BinanceSpotCollector uses Convert API for Ondo tokens"
```

---

## Task 3: Update Strategy to Use Auto-Derived Spot Symbols

**Context:** `funding_carry.py` references `underlying.binance_s` which will no longer exist after Task 1. Must use `underlying.binance_spot` property instead.

**Files:**
- Modify: `src/fund_rate_arb/strategies/funding_carry.py:109,149`
- Test: `tests/strategies/test_funding_carry.py`

**Step 1: Update `select()` method**

Replace line 109:
```python
if u.binance_f is None or u.binance_s is None:
```
with:
```python
if u.binance_f is None or u.binance_spot is None:
```

**Step 2: Update `open_paired_position()` method**

Replace line 149:
```python
spot_symbol = underlying.binance_s
```
with:
```python
spot_symbol = underlying.binance_spot
```

**Step 3: Run tests**

```bash
uv run pytest tests/strategies/ -v
```

**Step 4: Commit**

```bash
git add src/fund_rate_arb/strategies/funding_carry.py tests/strategies/test_funding_carry.py
git commit -m "refactor: use auto-derived binance_spot property"
```

---

## Task 4: Fix Monitor to Use Live Prices

**Context:** `monitor_position()` at `funding_carry.py:238-246` sets `current_mark = position.entry_price` and `current_index = position.entry_price`. Basis drift and PnL are always zero.

**Files:**
- Modify: `src/fund_rate_arb/strategies/funding_carry.py:227-281`
- Test: `tests/strategies/test_funding_carry.py`

**Step 1: Write failing test**

```python
def test_monitor_uses_live_prices_not_entry_prices():
    """Monitor should fetch current market prices, not use stale entry prices."""
```

**Step 2: Implement live price fetch in monitor_position**

Replace lines 238-246 in `funding_carry.py`:

```python
live_mark = self._get_mark_price(symbol, db_path)
live_index = self._get_index_price(symbol, db_path)

market = MarketData(
    symbol=symbol,
    exchange=exchange,
    current_mark=live_mark or position.entry_price,
    current_index=live_index or position.entry_price,
    current_basis=(live_mark - live_index) / live_index if live_index else 0.0,
    funding_history_48h=funding_48h,
    oi_window_8h=oi_8h,
)
```

**Step 3: Add `_get_index_price()` helper**

```python
def _get_index_price(self, symbol: str, db_path: str) -> float:
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
```

**Step 4: Wire up `compute_basis_drift()` in monitor**

After `exit_engine.check_all()`, add basis drift check:

```python
from fund_rate_arb.data.monitors import compute_basis_drift

basis_drift = compute_basis_drift(
    market.current_mark, market.current_index, position.entry_basis
)
if basis_drift > 0.02:
    exits.append(ExitSignal(
        position_execution_id=position.execution_id,
        rule_type="basis_drift",
        severity="critical",
        message=f"Basis drift {basis_drift:.4f} exceeds 2% threshold",
    ))
```

**Step 5: Run tests + commit**

```bash
uv run pytest tests/strategies/ -v
git add src/fund_rate_arb/strategies/funding_carry.py tests/strategies/test_funding_carry.py
git commit -m "fix: monitor uses live prices for basis drift detection"
```

---

## Task 5: Add Stop-Loss Exit Rule

**Context:** No exit rule based on unrealized PnL. Unlimited downside risk on basis widening.

**Files:**
- Modify: `src/fund_rate_arb/risk/exit_engine.py`
- Test: `tests/risk/test_exit_engine.py`

**Step 1: Write failing test**

```python
def test_max_loss_rule_triggers_on_pnl_threshold():
    rule = MaxLossRule(max_loss_pct=5.0)
    # position at entry_price=200, current_mark=190, notional=400
    # loss = |190-200| * contracts / notional * 100 = 5%
    signals = rule.check(position, market)
    assert signals[0].severity == "critical"
    assert signals[0].rule_type == "max_loss"
```

**Step 2: Implement MaxLossRule**

```python
class MaxLossRule(ExitRule):
    """Exit when unrealized loss exceeds threshold percentage of notional."""

    def __init__(self, max_loss_pct: float = 5.0):
        self.max_loss_pct = max_loss_pct

    def check(self, position: CarryPosition, market: MarketData) -> list[ExitSignal]:
        if position.notional_usdt == 0 or position.entry_price == 0:
            return []
        price_loss = abs(market.current_mark - position.entry_price) * position.contracts
        loss_pct = price_loss / position.notional_usdt * 100
        if loss_pct >= self.max_loss_pct:
            return [ExitSignal(
                position_execution_id=position.execution_id,
                rule_type="max_loss",
                severity="critical",
                message=f"Loss {loss_pct:.1f}% exceeds {self.max_loss_pct}% threshold",
            )]
        return []
```

**Step 3: Wire into ExitRuleEngine in `main.py:90-95`**

Add `MaxLossRule(max_loss_pct=5.0)` to the rules list.

**Step 4: Run tests + commit**

```bash
uv run pytest tests/risk/ -v
git add src/fund_rate_arb/risk/exit_engine.py tests/risk/test_exit_engine.py src/fund_rate_arb/main.py
git commit -m "feat: add MaxLossRule for stop-loss risk control"
```

---

## Task 6: Promote APYThresholdRule to Critical Severity

**Files:**
- Modify: `src/fund_rate_arb/risk/exit_engine.py:87`
- Test: `tests/risk/test_exit_engine.py`

**Step 1: Change severity**

In `exit_engine.py:87`, change `severity="warning"` to `severity="critical"`.

**Step 2: Run tests + commit**

```bash
uv run pytest tests/risk/test_exit_engine.py -v
git add src/fund_rate_arb/risk/exit_engine.py tests/risk/test_exit_engine.py
git commit -m "fix: APYThresholdRule severity critical for auto-exit"
```

---

## Task 7: Promote OI Spike and Regime Shift to Critical

**Files:**
- Modify: `src/fund_rate_arb/strategies/funding_carry.py:252,270`

**Step 1: Change both `severity="warning"` to `severity="critical"`**

**Step 2: Run tests + commit**

```bash
uv run pytest tests/strategies/ -v
git add src/fund_rate_arb/strategies/funding_carry.py
git commit -m "fix: OI spike and regime shift severity critical for auto-exit"
```

---

## Task 8: Wire Up Allocator for Capital Management

**Files:**
- Modify: `src/fund_rate_arb/strategies/funding_carry.py`
- Modify: `src/fund_rate_arb/main.py`

**Step 1: Integrate Allocator into FundingCarry**

```python
def __init__(self, ..., allocator: Allocator | None = None):
    self.allocator = allocator or Allocator(
        total_capital=notional_per_leg * max_positions,
        max_concurrent=max_positions,
        notional_per_leg=notional_per_leg,
    )
```

**Step 2: Use `self.allocator.can_allocate()` / `.allocate()` / `.release()` in select/open/close**

**Step 3: Run tests + commit**

```bash
uv run pytest tests/ -v
git add src/fund_rate_arb/strategies/funding_carry.py src/fund_rate_arb/main.py
git commit -m "feat: integrate Allocator for capital management"
```

---

## Task 9: Select by Weekly Score for ~1 Week Holding Preference

**Files:**
- Modify: `src/fund_rate_arb/strategies/funding_carry.py:87-121`
- Modify: `src/fund_rate_arb/signal/detector.py`

**Step 1: Enrich signals with 72h history in `_fetch_signals`**

After `detect_signals()`, call `rank_signals()` with history from DB.

**Step 2: Sort candidates by `score_weekly` instead of raw `apy_net`**

**Step 3: Run tests + commit**

```bash
uv run pytest tests/ -v
git add src/fund_rate_arb/strategies/funding_carry.py src/fund_rate_arb/signal/detector.py
git commit -m "feat: select by weekly score for ~1 week holding preference"
```

---

## Summary

| Task | Impact | Risk |
|------|--------|------|
| 1. Auto-derive spot symbols | Simplifies config, removes redundancy | Low — property-based |
| 2. Convert API collector | Enables actual spot leg execution | Medium — new API integration |
| 3. Update strategy references | Aligns with new model | Low — simple find/replace |
| 4. Live prices in monitor | Makes monitoring detect real problems | Low — fallback to entry price |
| 5. Stop-loss rule | Prevents unlimited downside | Threshold tuning needed |
| 6. APY critical | Auto-exit unprofitable positions | May exit aggressively |
| 7. Anomaly critical | Auto-exit on OI/regime anomalies | May false-positive |
| 8. Allocator | Proper capital tracking | Low — wraps existing logic |
| 9. Weekly score | Prefers stable ~1 week APY | Low — sort order change |

**Execution order:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9

**Dependencies:**
- Task 3 depends on Task 1 (model change)
- Task 2 is independent (new collector)
- Tasks 4-9 are independent of each other
