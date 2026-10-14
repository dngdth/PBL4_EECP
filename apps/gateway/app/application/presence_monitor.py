import asyncio
import logging

from apps.gateway.app.application.ports import PresenceRefresher

logger = logging.getLogger(__name__)


class PresenceMonitor:
    """Keep local liveness monitoring running through uplink backpressure."""

    def __init__(self, router: PresenceRefresher, stale_seconds: float, offline_seconds: float):
        self._router = router
        self._stale_seconds = stale_seconds
        self._offline_seconds = offline_seconds

    async def run(self) -> None:
        while True:
            await asyncio.sleep(max(0.1, self._stale_seconds / 2))
            try:
                await self._router.refresh_presence(self._stale_seconds, self._offline_seconds)
            except OSError:
                logger.warning("Gateway presence publication deferred; uplink unavailable")
