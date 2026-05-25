---
name: funding-arb
description: Funding rate arbitrage screener. Fetches funding rates from Binance and Hyperliquid, computes quality scores, and ranks cross-exchange arbitrage opportunities. Use when the user asks about funding rates, carry trades, funding arbitrage, or runs /funding-arb.
---

# Funding Rate Arbitrage Screener

## Scope

**Whitelist only:** scans restricted to TradeFI US stock tickers. Defined in `config.py`.

### Hyperliquid (18 HIP-3 equity perps)
`TSLAUSDT`, `NVDAUSDT`, `AAPLUSDT`, `MSFTUSDT`, `AMZNUSDT`, `METAUSDT`, `GOOGLUSDT`, `PLTRUSDT`, `MSTRUSDT`, `COINUSDT`, `AMDUSDT`, `HOODUSDT`, `ORCLUSDT`, `INTCUSDT`, `SPCXUSDT`, `OPENAIUSDT`, `SP500USDT`, `XYZ100USDT`

### Binance (23 Ondo tokenized stocks on Binance Alpha)
`AAPLon`, `TSLAon`, `NVDAon`, `GOOGLon`, `METAon`, `AMZNon`, `MSFTon`, `NFLXon`, `CRCLon`, `QQQon`, `COINon`, `HOODon`, `PLTRon`, `MUon`, `ORCLon`, `INTCon`, `MSTRon`, `ABNBon`, `JDon`, `BABAon`, `SLVon`, `XYZon`, `MTZon`

**WARNING:** Binance Ondo tokens are spot assets on Binance Alpha, NOT on `fapi.binance.com` (Futures API). `BinanceCollector` returns empty for these until a spot collector is added. Cross-exchange arb only works for symbols present on both exchanges with same symbol format.

- Expand whitelist: edit `WHITELIST_BINANCE` / `WHITELIST_HYPERLIQUID` in `config.py`

## Triggers
- `/funding-arb`
- "funding rate", "carry trade", "funding arbitrage", "funding screener"

## Usage

```bash
# Fetch latest data from both exchanges
uv run -p fund_rate_arb fund-rate-arb fetch --all

# Score and rank top opportunities
uv run -p fund_rate_arb fund-rate-arb score --top 20

# Show cross-exchange arbitrage opportunities
uv run -p fund_rate_arb fund-rate-arb arb-opportunities

# Check history for a specific symbol
uv run -p fund_rate_arb fund-rate-arb history -s BTCUSDT -d 30
```

## Interpretation

**Quality Score Components:**
- `Funding Mean`: Average funding rate (higher positive = better for carry)
- `Persistence`: % of intervals with positive funding (stability matters more than spikes)
- `Volatility`: Std dev of funding (lower = more predictable carry)
- `OI Stability`: Open interest stability (stable OI = sustained demand)
- `Spread Cost`: Bid-ask spread cost (lower = cheaper to enter/exit)

**Key Metrics:**
- **APY%**: Annualized funding income (funding_rate × 1095 intervals/year)
- **Break-even**: Days needed to recover entry+exit costs from funding income
- **Regime**: bull (structurally positive), bear (negative), neutral

**Arbitrage Opportunities:**
- Shows symbols where Binance and Hyperliquid funding rates diverge
- `Direction` indicates which exchange to long spot + short perp on
- `Diff APY%` is the annualized profit from the differential (before fees)

## Caveats
- Data must be fetched before scoring
- Phase 1: REST snapshots only, no historical backfill
- Scans whitelist-only (TSLA, NVDA); expand whitelist in config.py to add symbols
- Cross-exchange matching assumes same coin symbols (TSLAUSDT, NVDAUSDT)
- Fees use default retail tier; adjust for VIP tiers
