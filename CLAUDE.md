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
uv run fund-rate-arb fetch              # Collect data from exchanges
uv run fund-rate-arb scan               # Continuous signal scanner (polling loop)
uv run fund-rate-arb score --top 20     # Score and rank
uv run fund-rate-arb signals            # Fetch + detect + print TG table
uv run fund-rate-arb arb-opportunities  # Cross-exchange arb
uv run fund-rate-arb pm-status          # Portfolio margin account status
uv run fund-rate-arb trade              # Execute single PM order
uv run fund-rate-arb close              # Close PM position
uv run fund-rate-arb report --min-apy 10 # Generate markdown report
uv run fund-rate-arb history -s NVDA    # Funding rate history
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
- **`.env` uses `TG_BOT_TOKEN` / `TG_CHAT_ID`** for Telegram alerts. Settings `extra="ignore"` so `APY_THRESHOLD` etc. pass through to `main.py` via `os.environ`.
- **Binance API key** IP-whitelisted to VPS only (139.180.196.53). All authenticated Binance API calls (spot, fapi, convert) must run on VPS. Local dev has no API access.
- **VPS proxy = null** — Japan VPS accesses Binance directly. Local dev uses `http://127.0.0.1:7897`.

## Deployment

**VPS:** Vultr Japan, Ubuntu 22.04, user `openclaw`, hostname `vultr`, SSH alias `tvps`

**Service:** user-level systemd (`systemctl --user`), no sudo needed

**Files:**
- `deploy/fund-rate-scan.service` — systemd unit template
- `deploy/install.sh` — installs units to `~/.config/systemd/user/`
- `~/.env` — TG credentials + signal thresholds
- `~/settings.yaml` — operational params (binance_proxy: null on VPS)
- `~/logs/scan.log` — application log

**Daily deploy:**
```bash
ssh tvps "cd ~/fund_rate_arb && git pull && ~/.local/bin/uv sync && systemctl --user restart fund-rate-scan"
```

**Monitor:**
```bash
ssh tvps "systemctl --user status fund-rate-scan"
ssh tvps "journalctl --user -u fund-rate-scan -f"
```

**TG markdown:** `escape_md_v2()` escapes `\` first, skips content inside triple-backtick code blocks.

## Deployment Workflow

**Rule:** All code changes → commit → push to GitHub → deploy to VPS. Secrets (.env) → scp/ssh directly to VPS, **NEVER commit**.

**Deploy command:**
```bash
# Push code changes
git add ... && git commit -m "..." && git push

# Deploy to VPS
ssh tvps "cd ~/fund_rate_arb && git pull && ~/.local/bin/uv sync && systemctl --user restart fund-rate-scan"

# Push secrets to VPS (NEVER commit)
scp src/fund_rate_arb/.env tvps:~/fund_rate_arb/.env
```

**Binance API key:** IP-whitelisted to VPS only (139.180.196.53). Works on VPS, fails elsewhere.

## Testing

pytest with asyncio auto mode. Run: `uv run pytest -v`
