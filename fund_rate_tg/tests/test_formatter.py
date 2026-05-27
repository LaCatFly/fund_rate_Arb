import pytest

from fund_rate_tg.formatter import format_signals, escape_md_v2
from fund_rate_tg.models import Signal


def test_empty_signals():
    result = format_signals([])
    assert "No signals" in result


def test_single_signal():
    sig = Signal(
        exchange="BN",
        symbol="BTC",
        apy_net=18.5,
        apy_gross=19.2,
        cost=0.7,
        basis_pct=0.15,
        spread_bps=1.2,
        interval_h=8,
    )
    result = format_signals([sig])
    assert "1 signals" in result
    assert "BN BTC APY 18.5%" in result
    assert "gross 19.2%" in result
    assert "cost 0.7%" in result
    assert "+0.15%" in result
    assert "1.2bp" in result
    assert "8h" in result


def test_negative_basis():
    sig = Signal(
        exchange="HL",
        symbol="ETH",
        apy_net=22.1,
        apy_gross=23.0,
        cost=0.9,
        basis_pct=-0.05,
        spread_bps=0.8,
        interval_h=1,
    )
    result = format_signals([sig])
    assert "-0.05%" in result


def test_multiple_signals():
    signals = [
        Signal("BN", "BTC", 18.5, 19.2, 0.7, 0.15, 1.2, 8),
        Signal("HL", "ETH", 22.1, 23.0, 0.9, -0.05, 0.8, 1),
        Signal("BN", "SOL", 16.3, 17.0, 0.7, 0.20, 2.1, 8),
    ]
    result = format_signals(signals)
    assert "3 signals" in result
    lines = result.strip().split("\n")
    assert len(lines) == 5  # header + blank + 3 signals


def test_escape_md_v2():
    result = escape_md_v2("Hello_world (test) 100%")
    assert "\\_" in result
    assert "\\(" in result
    # % is not a MarkdownV2 special char, should be unchanged
    assert "100%" in result
