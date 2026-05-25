"""Tests for data models."""

from datetime import datetime

from fund_rate_arb.models.funding import FundingRate, OpenInterest, SpreadData


class TestFundingRate:
    def test_to_db_row(self):
        fr = FundingRate(
            symbol="BTCUSDT",
            exchange="binance",
            timestamp=datetime(2025, 1, 1, 0, 0, 0),
            funding_rate=0.0001,
            predicted_rate=0.00012,
            mark_price=50000.0,
            index_price=49999.5,
        )
        row = fr.to_db_row()
        assert row[0] == "BTCUSDT"
        assert row[1] == "binance"
        assert row[3] == 0.0001
        assert row[4] == 0.00012
        assert row[5] == 50000.0


class TestOpenInterest:
    def test_to_db_row(self):
        oi = OpenInterest(
            symbol="ETHUSDT",
            exchange="binance",
            timestamp=datetime(2025, 1, 1),
            open_interest=123456.78,
        )
        row = oi.to_db_row()
        assert row[0] == "ETHUSDT"
        assert row[3] == 123456.78


class TestSpreadData:
    def test_spread_bps(self):
        spread = SpreadData(
            symbol="BTCUSDT",
            exchange="hyperliquid",
            timestamp=datetime(2025, 1, 1),
            bid=50000.0,
            ask=50001.0,
            spread_bps=0.2,
        )
        assert spread.spread_bps == 0.2
