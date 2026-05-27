import pytest
from unittest.mock import AsyncMock, patch

from fund_rate_tg.sender import send_signals
from fund_rate_tg.models import Signal


@pytest.mark.asyncio
async def test_send_success():
    sig = Signal("BN", "BTC", 18.5, 19.2, 0.7, 0.15, 1.2, 8)

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None

    with patch("fund_rate_tg.sender.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_resp
        result = await send_signals([sig])
        assert result is True


@pytest.mark.asyncio
async def test_send_retry_then_fail():
    sig = Signal("BN", "BTC", 18.5, 19.2, 0.7, 0.15, 1.2, 8)

    with patch("fund_rate_tg.sender.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post.side_effect = Exception("timeout")
        result = await send_signals([sig])
        assert result is False
