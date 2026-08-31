import asyncio
import structlog
from typing import Any, Callable, Awaitable, NamedTuple
from .base import BaseQueue
from app.core.config import settings

logger = structlog.get_logger()

class APIRequest(NamedTuple):
    task: Callable[..., Awaitable[Any]]
    args: tuple
    kwargs: dict
    future: asyncio.Future
    priority: int
    attempts: int = 0

class APIQueue(BaseQueue):
    def __init__(self, interval: Optional[float] = None):
        self._queue = asyncio.PriorityQueue()
        self._interval = interval or settings.api_request_interval
        self._running = True
        self._counter = 0
        self._retry_delays = [1, 5, 15]
        self._worker_task = asyncio.create_task(self._worker())

    async def enqueue(self, task: Callable[..., Awaitable[Any]], *args, priority: int = 1, **kwargs):
        future = asyncio.get_running_loop().create_future()
        request = APIRequest(task, args, kwargs, future, priority)
        
        self._counter += 1
        # Sequence prevents comparing Callables in PriorityQueue
        await self._queue.put((priority, self._counter, request))
        return await future

    async def _worker(self):
        while self._running:
            try:
                priority, sequence, request = await self._queue.get()
                
                try:
                    result = await request.task(*request.args, **request.kwargs)
                    if not request.future.done():
                        request.future.set_result(result)
                except Exception as e:
                    if request.attempts < len(self._retry_delays):
                        delay = self._retry_delays[request.attempts]
                        logger.warning("api_request_retry", attempt=request.attempts+1, delay=delay, error=str(e))
                        
                        retry_request = request._replace(attempts=request.attempts + 1)
                        # Schedule retry
                        asyncio.create_task(self._schedule_retry(priority, retry_request, delay))
                    else:
                        logger.error("api_request_failed_after_retries", error=str(e))
                        if not request.future.done():
                            request.future.set_exception(e)
                finally:
                    self._queue.task_done()

                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("queue_worker_error", error=str(e))

    async def _schedule_retry(self, priority, request, delay):
        await asyncio.sleep(delay)
        self._counter += 1
        await self._queue.put((priority, self._counter, request))

    async def process_next(self):
        pass

    async def stop(self):
        self._running = False
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
