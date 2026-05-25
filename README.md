# Fund Rate Arb

Funding rate arbitrage screener for Binance and Hyperliquid.

## Quick Start

```bash
uv sync

# Fetch data
uv run fund-rate-arb fetch --all

# Score and rank
uv run fund-rate-arb score --top 20

# Cross-exchange opportunities
uv run fund-rate-arb arb-opportunities
```

## Phase 1 Scope

- Data collection from Binance and Hyperliquid REST APIs
- Funding quality scoring (persistence, volatility, cost-adjusted carry)
- Cross-exchange arbitrage detection
- SQLite storage
