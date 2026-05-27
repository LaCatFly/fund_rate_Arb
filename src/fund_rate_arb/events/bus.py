"""Simple in-memory pub/sub event bus."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)


class EventBus:
    """In-memory publish/subscribe event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Register a handler for an event type."""
        self._handlers[event_type].append(handler)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to all registered handlers."""
        for handler in self._handlers.get(event_type, []):
            try:
                handler(payload)
            except Exception:
                logger.exception("Event handler failed: %s", event_type)
