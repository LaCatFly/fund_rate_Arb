from datetime import datetime, timezone

import pytest

from fund_rate_arb.signal.detector import detect_signals, calc_cost_pct
from fund_rate_arb.models.funding import FundingRate, SpreadData


def _fr(exchange, symbol, rate, mark_price=50000.0):
    return FundingRate(
        symbol=symbol,
        exchange=exchange,
        timestamp=datetime.now(timezone.utc),
        funding_rate=rate,
        mark_price=mark_price,
        index_price=mark_price,
    )


def _spread(exchange, symbol, spread_bps, bid=50000.0):
    ask = bid * (1 + spread_bps / 10000)
    return SpreadData(
        symbol=symbol,
        exchange=exchange,
        timestamp=datetime.now(timezone.utc),
        bid=bid,
        ask=ask,
        spread_bps=spread_bps,
    )


def test_no_signals_below_threshold():
    funding = [_fr("binance", "BTC", 0.00001)]  # ~0.01 per 8h = 3.65% APY
    spreads = [_spread("binance", "BTC", 1.0)]
    signals = detect_signals(funding, spreads, apy_threshold=15.0)
    assert len(signals) == 0


def test_signal_above_threshold():
    # 0.0003 per 8h = 0.0003 * 1095 = 0.3285 = 32.85% APY gross
    funding = [_fr("binance", "BTC", 0.0003)]
    spreads = [_spread("binance", "BTC", 1.0)]
    signals = detect_signals(funding, spreads, apy_threshold=15.0)
    assert len(signals) == 1
    assert signals[0].exchange == "BN"
    assert signals[0].symbol == "BTC"
    assert signals[0].apy_gross > 15.0


def test_hyperliquid_signal():
    # 0.00002 per hour = 0.00002 * 8760 = 0.1752 = 17.52% APY gross
    funding = [_fr("hyperliquid", "ETH", 0.00002, mark_price=3000.0)]
    spreads = [_spread("hyperliquid", "ETH", 2.0, bid=3000.0)]
    signals = detect_signals(funding, spreads, apy_threshold=15.0)
    assert len(signals) == 1
    assert signals[0].exchange == "HL"
    assert signals[0].interval_h == 1


def test_spread_filter():
    funding = [_fr("binance", "BTC", 0.0003)]
    spreads = [_spread("binance", "BTC", 15.0)]  # above max_spread_bps=10.0
    signals = detect_signals(funding, spreads, apy_threshold=15.0, max_spread_bps=10.0)
    assert len(signals) == 0


def test_missing_spread():
    funding = [_fr("binance", "BTC", 0.0003)]
    signals = detect_signals(funding, [], apy_threshold=15.0)
    assert len(signals) == 0


def test_calc_cost():
    # maker 0.02%, taker 0.05%, spread 1 bps = 0.01%
    cost = calc_cost_pct(1.0, 0.0002, 0.0005)
    # round trip = (0.0002 + 0.0005) * 100 = 0.07%, spread = 0.01%, total = 0.08%
    assert cost == pytest.approx(0.08, abs=0.01)


def test_signal_fields():
    funding = [_fr("binance", "SOL", 0.0003, mark_price=100.0)]
    spreads = [_spread("binance", "SOL", 2.0, bid=100.0)]
    signals = detect_signals(funding, spreads, apy_threshold=15.0)
    s = signals[0]
    assert s.apy_net > 0
    assert s.cost > 0
    assert s.interval_h == 8
    assert s.spread_bps > 0
