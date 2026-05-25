"""Fetch all Binance USDT perpetual futures contracts and identify stock-like ones."""

import httpx
import json
from pathlib import Path

OUTPUT = Path(__file__).parent / "binance_futures_stocks.json"
FAPI = "https://fapi.binance.com"

client = httpx.Client(base_url=FAPI, timeout=30)

# 1. Get all exchange info (USDT-M perpetuals)
resp = client.get("/fapi/v1/exchangeInfo")
resp.raise_for_status()
info = resp.json()

# 2. Get premiumIndex for funding rates
prem_resp = client.get("/fapi/v1/premiumIndex")
prem_resp.raise_for_status()
premium_index = {item["symbol"]: item for item in prem_resp.json()}

# Known crypto + commodities + fiat bases to skip
SKIP = {
    # Crypto
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT",
    "LINK", "MATIC", "SHIB", "LTC", "BCH", "TRX", "NEAR", "APT",
    "ARB", "OP", "FIL", "ATOM", "ICP", "SUI", "SEI", "TIA",
    "PEPE", "WIF", "FLOKI", "BONK", "INJ", "STX", "IMX",
    "RENDER", "RUNE", "GRT", "ALGO", "FTM", "SAND", "MANA", "AXS",
    "AAVE", "UNI", "SUSHI", "CRV", "MKR", "COMP", "SNX", "1INCH",
    "ENJ", "CHZ", "GALA", "APE", "GMT", "GST", "LDO", "BLUR",
    "PEOPLE", "MASK", "CFX", "HIFI", "WLD", "RDNT", "MANTA", "STRK",
    "PENDLE", "ENA", "W", "NOT", "IO", "ZK", "ZRO", "ZRX", "TON",
    "TAO", "TONCOIN", "USDC", "USDT", "DAI", "FDUSD", "TUSD",
    "EUR", "GBP", "TRY", "BRL", "ARS", "BIDR", "VAI",
    # Commodities
    "XAU", "XAG", "OIL", "WTI", "BRENT",
    # Indices (crypto)
    "DEFI", "CEX", "METVERSE",
}

stock_perps = []
for sym_info in info["symbols"]:
    if sym_info["contractType"] != "PERPETUAL":
        continue
    symbol = sym_info["symbol"]
    if not symbol.endswith("USDT"):
        continue

    base = symbol.removesuffix("USDT")
    if base in SKIP:
        continue
    # Skip if base has numbers (like 3L, 5L, BEAR, BULL, etc.)
    if any(c.isdigit() for c in base):
        continue

    premium = premium_index.get(symbol, {})
    stock_perps.append({
        "symbol": symbol,
        "base": base,
        "active": sym_info.get("status") == "TRADING",
        "mark_price": float(premium.get("markPrice", 0)),
        "funding_rate": float(premium.get("lastFundingRate", 0)),
        "index_price": float(premium.get("indexPrice", 0)),
    })

# Sort by base name
stock_perps.sort(key=lambda x: x["base"])

print(f"Found {len(stock_perps)} stock-like USDT perpetuals on Binance Futures:")
print()
for i, p in enumerate(stock_perps, 1):
    print(f"  {i:2d}. {p['base']:10s}  {p['symbol']:15s}  active={p['active']}  mark={p['mark_price']:.2f}  funding={p['funding_rate']:.6f}")

OUTPUT.write_text(json.dumps(stock_perps, indent=2))
print(f"\nSaved to {OUTPUT}")
