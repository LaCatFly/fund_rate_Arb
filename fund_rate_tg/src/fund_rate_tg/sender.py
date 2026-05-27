import asyncio
import logging

import httpx

from .config import settings
from .formatter import escape_md_v2, format_signals
from .models import Signal

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_RETRIES = 3
RETRY_DELAY = 2


async def send_signals(signals: list[Signal]) -> bool:
    text = format_signals(signals)
    escaped = escape_md_v2(text)

    url = TG_API.format(token=settings.tg_bot_token)
    payload = {
        "chat_id": settings.tg_chat_id,
        "text": escaped,
        "parse_mode": settings.tg_parse_mode,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                logger.info("TG sent: %d signals", len(signals))
                return True
        except httpx.HTTPError as e:
            logger.warning("TG attempt %d failed: %s", attempt, e)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("TG send failed after %d attempts", MAX_RETRIES)
                return False
        except Exception as e:
            logger.error("TG send error: %s", e)
            return False

    return False
