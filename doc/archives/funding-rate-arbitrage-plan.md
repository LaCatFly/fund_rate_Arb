# Funding Rate Arbitrage — Strategy Plan

> **Source:** repo reference `aoki-h-jp/funding-rate-arbitrage`
> **Strategy:** Delta Neutral Funding Carry (Spot Long + Perp Short)
> **Exchange:** Binance (Phase 1)

---

## Core Thesis

真正的 edge 不是找最高 funding，而是找：

- 长期稳定 positive funding
- 高 liquidity
- 低 fee/slippage
- 高 persistence
- 低 liquidation risk

**Sharpe-adjusted carry > headline APY**

---

## 一、现货跟现货套利策略 — 核心

### 收益来源

```
Funding Income
- Trading Fees
- Slippage
- Borrow Cost
- Spread Cost
- Liquidation Risk
= Net PnL
```

这不是方向交易。核心 alpha 在：

- 哪些币 funding 能持续
- 哪些币 funding 虽高但会瞬间 collapse
- 哪些币 liquidity 足够
- 哪些币容易 squeeze / ADL / wick

**"持续性" 比 "瞬时 funding 高" 更重要。**

---

## 二、Step 1：期货核心参数

### 1. Funding Rate 机制

- 每 8 小时结算一次（Binance）
- Positive funding: Long pay Short
- Negative funding: Short pay Long
- 策略：Long Spot + Short Perp => 收 positive funding
- 关键：`Expected Future Funding > Total Cost`

### 2. Margin Mode

| Mode | 优点 | 缺点 |
|------|------|------|
| Cross Margin | 不容易爆仓，适合 carry | 整个账户风险联动 |
| Isolated Margin | 风险隔离 | 更容易 liquidation |

**建议：** Funding arbitrage 用 Cross margin，因为不是赌方向。

### 3. Leverage

- 建议：1x ~ 3x
- 收益来自 carry，不是 price movement
- 高 leverage 放大 liquidation risk / basis volatility / ADL 风险

### 4. Maintenance Margin

- Spot 盈利 ≠ Perp 自动补保证金
- 交易所不会帮你 cross-venue netting
- 极端上涨：spot +20%, perp short -20%
- 如果 perp 保证金不足 => 被强平 => 变成 naked long
- **这是 funding strategy 最大风险之一**

### 5. Mark Price vs Index Price

- Binance liquidation 看 Mark Price，不是 last trade
- 必须监控 mark/index spread
- Altcoin 会出现 mark manipulation / thin liquidity / premium spikes

### 6. Open Interest

- Funding 持续性通常来自：high OI + persistent long imbalance
- OI 爆炸 + funding 极高 = crowded trade
- 后面容易：funding collapse / long squeeze

---

## 三、Step 2：手续费与盈利条件

### 基础成本模型

假设：
- maker fee: 0.02%
- taker fee: 0.05%
- slippage: 0.03%
- spread cost: 0.02%

Round-trip (entry + exit): ≈ 0.15% ~ 0.25%

### Funding 收益估算

```
0.01% / 8h * 3 * 365 ≈ 10.95% annualized
```

但真实情况：funding 不持续，会 mean revert，需要扣手续费。

### 关键指标：Break-even Holding Time

```
Holding Time Needed = Total Entry/Exit Cost / Expected Net Funding Per Day
```

示例：
- cost = 0.2%
- net funding/day = 0.04%
- => 5 days break even

如果 historical persistence < 5 days，不值得做。

---

## 四、Target 1：筛选好的 Funding Rate

### Funding Quality Score

| Feature | 说明 |
|---------|------|
| **Funding Persistence** | 过去 30 天 positive ratio，positive_intervals / total_intervals |
| **Funding Volatility** | 低 volatility 更好，要稳定 carry |
| **Open Interest Stability** | 高且稳定 OI = 持续 speculative demand |
| **Basis Stability** | (perp - spot) / spot，unstable = violent reversion |
| **Liquidity** | 过滤 low volume / wide spread / shallow book |
| **Market Cap / Turnover** | 先从 Top 30-50 liquidity coins 开始 |
| **Long/Short Ratio** | 辅助 feature |
| **Funding Regime Detection** | Bull: structurally positive, Bear: often negative |

### 综合评分公式

```
Score = w1 * Funding Mean
      + w2 * Persistence
      - w3 * Volatility
      + w4 * OI Stability
      - w5 * Spread Cost
      - w6 * Slippage
```

---

## 五、Target 2：回测机制

### 最低限度回测

**数据需求：**
- Spot: OHLCV, orderbook spread
- Futures: funding history, OI, mark price, liquidation spikes

**回测逻辑：**

```python
每个 funding interval:
    if score > threshold:
        open trade

PnL = Funding PnL + Spot PnL - Perp PnL - Fees - Slippage
```

### Event Driven Backtest

Funding settlement 是离散事件，不是普通 bar strategy。

必须模拟：fees, latency, spread widening, partial fills

---

## 六、Target 3：退出机制 / 风控

| 退出条件 | 说明 |
|----------|------|
| **Funding Collapse** | expected funding < threshold => 立即退出 |
| **Funding Flip** | positive -> negative => 立刻平仓 |
| **Basis Compression** | basis rapidly mean reverting => carry 消失 |
| **OI Crash** | OI sudden drop = crowded trade unwind |
| **Liquidation Buffer** | 动态监控 distance_to_liquidation |
| **Volatility Stop** | realized volatility 爆炸 => reduce exposure |

---

## 七、数据层设计

### Phase 1：直接用 Binance API

单交易所 carry，做透。

### 需要的数据

**REST:**
- `/fapi/v1/fundingRate` — Funding
- `/openInterest` — Open Interest
- `/premiumIndex` — Premium Index

**WebSocket (必须):**
- Mark price
- Orderbook
- Funding updates

### 数据库：ClickHouse

典型 time-series 高频结构，非常适合 funding history / orderbook snapshots / OI history。

### 表设计

```sql
funding_rates (symbol, timestamp, funding_rate, predicted_rate, mark_price, index_price)
oi_snapshots  (symbol, timestamp, open_interest)
spreads       (symbol, timestamp, bid_ask_spread, depth_1pct)
```

---

## 八、部署层

### Phase 1（研究阶段）

Python + ccxt + asyncio + Postgres

### Phase 2（正式运行）

- **数据服务：** Rust / Go（websocket ingestion）
- **Strategy Engine：** Python（方便 research）
- **Execution Engine：** Rust / Go（降低 latency / crash / reconnect issues）

### 服务器

Tokyo / Singapore VPS — Binance matching engine latency 更低。

---

## 九、Roadmap

| Phase | 内容 | 重点 |
|-------|------|------|
| **1. Data Collection** | funding history, OI, spread, mark/index | 持续收集 1-2 个月 |
| **2. Research Notebook** | funding persistence, mean reversion, holding period, fee-adjusted pnl | alpha research |
| **3. Simple Backtest** | Spot Long + Perp Short | 不做 multi-exchange |
| **4. Paper Trading** | real slippage, liquidation buffer, funding drift | 实盘模拟 |
| **5. Production** | portfolio allocation, multi-symbol optimization, execution engine | auto risk management |

---

## 十、推荐参考的开源 Repo

| Repo | 价值 |
|------|------|
| **aoki-h-jp/funding-rate-arbitrage** | 轻量、结构清晰、exchange abstraction 已做好 |
| **50shadesofgwei/funding-rate-arbitrage** | 偏 production architecture，有 position controller / profitability estimation |
| **Hummingbot** | 最完整 crypto execution framework，学习 connector architecture / order lifecycle |
| **kir1l/Funding-Arbitrage-Screener** | scanner-only，参考 concurrent fetching / fee-aware spread |
| **ccxt** | exchange compatibility layer，必用 |

### 推荐技术路线

1. **Research Infra:** Python + ccxt + asyncio + Binance API + Postgres/ClickHouse
2. **Alpha Research:** persistence score, funding prediction, basis mean reversion, OI analysis
3. **Execution Layer:** websocket, order manager, hedge sync, liquidation monitor
4. **Production:** Rust/Go, co-location, low latency

### 最推荐架构组合

**aoki-h-jp + Hummingbot** 组合学习

---

## 十一、正确阅读 Repo 的方式

不要 clone -> pip install -> run。

1. **画架构图：** exchange layer → data layer → signal layer → execution layer → risk layer
2. **研究数据流：** websocket → cache → strategy → order
3. **研究 state management：** 交易系统最难的是状态同步，不是 strategy

---

## 最终建议

先做 **Funding Research Platform**：
- funding database
- persistence analysis
- fee model
- pnl simulator

真正赚钱的 edge 不是 execution，而是：**哪些 funding 是可持续的。**
