# Funding Rate APY — Minimal Signal Plan

> **Scope:** Trading signal detection → Telegram notification only.
> **Phase:** MVP. No execution, no backtest, no DB.

---

## One Goal

Accurate APY estimate for funding rate carry (long spot + short perp). Signal fires when APY crosses threshold → send to TG.

---

## Data Sources

| Source | Mark Price | Funding Rate | Interval | Bid-Ask Spread |
|--------|-----------|--------------|----------|----------------|
| **Binance** | `GET /fapi/v1/premiumIndex` → `markPrice` | `lastFundingRate` (per 8h) | Every 8h | `GET /fapi/v1/ticker/bookTicker` |
| **Hyperliquid** | `POST /info` + `metaAndAssetCtxs` → `markPx` | `funding` (per hour) | Every 1h | `GET /api/price/{coin}` (Moon Dev proxy) or calc from `l2Book` |

### Key Differences

- **Binance funding**: 8-hour intervals. Annualize = `rate * 3 * 365`
- **Hyperliquid funding**: 1-hour intervals. Annualize = `rate * 24 * 365`
- **Hyperliquid API**: POST for reads, parallel arrays (`universe[i]` ↔ `asset_ctxs[i]`)
- **Moon Dev proxy**: GET-based, removes HL rate limits, pre-calculates spread. Requires `X-API-Key`.

---

## APY Formula (Simplified)

```
APY_net = APY_gross - Costs
```

### APY_gross

Use **Mark Price** from both sources for consistency.

```
APY_gross = annualized_funding_rate
```

Where:
- Binance: `lastFundingRate * 3 * 365`
- Hyperliquid: `funding * 24 * 365`

### Costs

```
Costs = trading_fees + spread_cost + basis_impact
```

| Component | Binance | Hyperliquid |
|-----------|---------|-------------|
| **Maker fee** | 0.02% | ~0.02% (varies by tier) |
| **Taker fee** | 0.05% | ~0.035% (varies by tier) |
| **Round-trip fees** | `(maker + taker) * 2` | `(maker + taker) * 2` |
| **Bid-ask spread cost** | `(ask - bid) / mid` | `(ask - bid) / mid` |

### Basis Impact (Futures-Spot Spread)

Track as **separate indicator** in this version. Not yet blended into APY.

```
basis_bps = (perp_mark_price - spot_mark_price) / spot_mark_price * 10000
```

- Positive basis: perp trades at premium (normal in bull market)
- Negative basis: perp trades at discount
- Large basis changes = entry/exit slippage

---

## Signal Logic

### Inputs
- Symbol list (configurable, start with top 10 by volume)
- APY threshold (e.g., 15% annualized net)
- Minimum liquidity filter (volume > $X, spread < Y bps)

### Detection

```
for each symbol:
    fetch_binance_data(symbol)
    fetch_hyperliquid_data(symbol)

    apy_bn = calc_apy_binance(funding_rate_bn, fees_bn, spread_bn)
    apy_hl = calc_apy_hyperliquid(funding_rate_hl, fees_hl, spread_hl)

    basis = calc_basis(perp_mark, spot_mark)

    if apy_bn > threshold AND liquidity_ok:
        signal("BINANCE", symbol, apy_bn, basis, spread_bn)

    if apy_hl > threshold AND liquidity_ok:
        signal("HYPERLIQUID", symbol, apy_hl, basis, spread_hl)
```

### Telegram Message Format

Batched. Max 3 lines per signal. Header shows scan time + count.

```
📊 Funding Scan | 2026-05-26 14:00 UTC | 3 signals

BN BTC APY 18.5% | gross 19.2% cost 0.7% | basis +0.15% spread 1.2bp 8h
HL ETH APY 22.1% | gross 23.0% cost 0.9% | basis -0.05% spread 0.8bp 1h
BN SOL APY 16.3% | gross 17.0% cost 0.7% | basis +0.20% spread 2.1bp 8h
```

Line format: `{EX} {SYM} APY {net}% | gross {gross}% cost {cost}% | basis {±x%} spread {y}bp {interval}`

No emojis per line. Dense. Scannable.

---

## Repo Structure

Two separate repos. TG skill is shared/imported by the server repo.

### Repo 1: `fund_rate_tg` — Telegram Notification Skill

Lightweight. One job: format signals → send TG messages. Reusable, exchange-agnostic.

```
fund_rate_tg/
├── pyproject.toml              # packaging, deps
├── README.md
├── .env.example                # TG_BOT_TOKEN, TG_CHAT_ID
│
├── src/fund_rate_tg/
│   ├── __init__.py
│   ├── config.py               # Settings: bot token, chat ID, parse mode
│   ├── formatter.py            # Signal → compact TG text (3-line format)
│   ├── sender.py               # TG Bot API: send_message, error handling, retry
│   └── models.py               # Signal dataclass (exchange, symbol, apy, basis, spread, interval)
│
└── tests/
    ├── test_formatter.py       # Verify compact format, edge cases (0 signals, many signals)
    └── test_sender.py          # Mock TG API, verify payload
```

### Repo 2: `fund_rate_arb` — Server Repo (deployed)

Polling engine + data fetchers + APY calc + signal detection. Imports `fund_rate_tg` for notifications.

```
fund_rate_arb/
├── pyproject.toml              # deps, includes fund_rate_tg as local/submodule dep
├── README.md
├── .env.example                # BINANCE_API, HL_API_KEY, MOONDEV_API_KEY, TG creds
├── docker/
│   ├── Dockerfile              # Production image
│   └── docker-compose.yml      # Service + optional cron/scheduler
│
├── src/fund_rate_arb/
│   ├── __init__.py
│   ├── config.py               # Symbols, thresholds, intervals, fee configs
│   ├── main.py                 # Entry: polling loop, error handling, graceful shutdown
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── binance.py          # Mark price, funding rate, bookTicker
│   │   └── hyperliquid.py      # metaAndAssetCtxs or Moon Dev proxy
│   │
│   ├── calc/
│   │   ├── __init__.py
│   │   ├── apy.py              # APY net = gross - costs (exchange-specific)
│   │   └── basis.py            # Perp-spot spread indicator
│   │
│   └── signal/
│       ├── __init__.py
│       ├── detector.py         # Threshold check, liquidity filter
│       └── scheduler.py        # Polling cadence per exchange (HL hourly, BN 8h)
│
├── tests/
│   ├── data/
│   │   ├── test_binance.py     # Mock API responses, verify parsing
│   │   └── test_hyperliquid.py # Mock API responses, verify parallel array mapping
│   ├── calc/
│   │   ├── test_apy.py         # APY calc accuracy, edge cases
│   │   └── test_basis.py       # Basis calc accuracy
│   └── signal/
│       └── test_detector.py    # Threshold logic, liquidity filter
│
└── deploy/
    ├── systemd.service         # Optional: systemd unit for VPS
    └── setup.sh                # Install + env setup script
```

### Dependency

`fund_rate_arb` imports `fund_rate_tg`:
- Dev: local path or git submodule
- Prod: pip install from private repo or vendored

Signal flow:
```
fund_rate_arb: data fetch → calc APY → detect signal → build Signal models
fund_rate_tg:   receive Signal[] → format compact → send to TG
```

---

## Next Steps (After This Version)

1. Add basis impact into APY (currently separate indicator)
2. Historical funding persistence check (not just current rate)
3. Multi-symbol portfolio ranking (pick top N signals)
4. Database for rate history
5. WebSocket for real-time updates instead of polling

---

## Decisions Made

- **Mark Price only** for spot+future in this version. Simplifies spot sourcing.
- **Basis tracked separately** as indicator, not blended into APY yet.
- **No execution layer** — signal only. TG is the output.
- **REST polling** in this version. WebSocket comes later.
- **Both exchanges** from day 1 since APIs are different enough to warrant early integration.
