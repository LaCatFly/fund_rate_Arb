# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project

Funding rate arbitrage screener and automated carry strategy for Binance and Hyperliquid. Python 3.11+, managed by `uv`. No framework — stdlib + httpx + click + rich + pydantic + ccxt + pyyaml.

## Structure

```
src/fund_rate_arb/
  config.py        — YAML-backed settings loader, Underlying model, whitelists
  db.py            — SQLite (WAL) schema + query helpers
  models/          — Pydantic data models
  collectors/      — Per-exchange data fetchers (base ABC)
  strategies/      — Strategy ABC + implementations (FundingCarry first)
  execution/       — Executor ABC, retry, allocator, paper/live executors
  data/            — Time-series retriever, sliding window monitors, payment tracker
  risk/            — RiskManager, ExitRuleEngine
  events/          — Simple pub/sub event bus
  scoring/         — Persistence analysis, fee model, quality score
  signal/          — Signal detection + polling scheduler
  tg/              — Telegram alerts
  cli/             — Click commands
  trading/         — Legacy (deprecated)

settings.yaml      — Operational params (strategies, weights, fees, exit rules)
settings.example.yaml — Template (committed)
fund_rate_arb.db   — SQLite database (gitignored)
```

## Commands

```bash
uv sync              # Install deps
uv run pytest        # Run tests
uv run fund-rate-arb fetch --all          # Collect data from exchanges
uv run fund-rate-arb score --top 20       # Score and rank
uv run fund-rate-arb arb-opportunities    # Cross-exchange arb
uv run fund-rate-arb strategy list        # List strategies
uv run fund-rate-arb strategy run --name funding_carry  # One cycle
uv run fund-rate-arb strategy status      # Open positions
```

## Data Flow

1. `fetch` → Collectors hit exchange REST APIs → Pydantic models → SQLite
2. `score` → Query latest → `compute_quality_score()` → ranked table
3. `strategy tick` → scan → select → execute → monitor → persist (hourly loop)

## Important Constraints

- **Binance stock perps exist ONLY on `fapi.binance.com`** — no equivalent on `api.binance.com` spot API. No separate spot collector needed. Basis = mark vs index price from perp API.
- **Hyperliquid HIP-3 equity perps** use `xyz:` namespace prefix. OI not available for HIP-3 assets.
- **SQLite uses `INSERT OR IGNORE`** with `UNIQUE(symbol, exchange, timestamp)` — duplicate inserts are silently dropped.
- **`settings.yaml`** is the single source of truth for operational params (strategies, thresholds, exit rules). Code defaults only when file is absent.
- **`settings.yaml` is gitignored** — `settings.example.yaml` is the committed template.
- **Paper mode first** — `execution.paper_mode: true` in settings before any live orders.

## Testing

pytest with asyncio auto mode. Run: `uv run pytest -v`

## Environment

- `BINANCE_PROXY`: HTTP proxy for Binance requests (default `http://127.0.0.1:7897`)
- `BINANCE_API_KEY` / `BINANCE_SECRET`: Required for portfolio margin execution
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`: Required for TG alerts
