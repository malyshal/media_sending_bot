import asyncio
import structlog
import time
from typing import Optional

logger = structlog.get_logger()

class RateLimiter:
    """
    Centralized rate limiter to ensure requests to JoyReactor
    do not exceed a specific frequency.
    """
    def __init__(self, min_interval: float = 2.5):
        self.min_interval = min_interval
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            current_time = time.time()
            elapsed = current_time - self._last_request_time
            
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                logger.info("rate_limit_sleep", sleep_time=f"{sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)
            
            self._last_request_time = time.time()
