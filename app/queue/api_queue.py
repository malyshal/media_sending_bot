import asyncio
import structlog
from typing import Any, Callable, Awaitable, NamedTuple
from .base import BaseQueue

logger = structlog.get_logger()

class APIRequest(NamedTuple):
    task: Callable[..., Awaitable[Any]]
    args: tuple
    kwargs: dict
    future: asyncio.Future
    priority: int
    attempts: int = 0

class APIQueue(BaseQueue):
    def __init__(self, interval: float = 2.0):
        self._queue = asyncio.PriorityQueue()
        self._interval = interval
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        self._retry_delays = [1, 5, 15] # as per TS

    async def enqueue(self, task: Callable[..., Awaitable[Any]], *args, priority: int = 1, **kwargs):
        future = asyncio.get_running_loop().create_future()
        request = APIRequest(task, args, kwargs, future, priority)
        await self._queue.put((priority, request))
        return await future

    async def _worker(self):
        while self._running:
            try:
                priority, request = await self._queue.get()
                
                try:
                    result = await request.task(*request.args, **request.kwargs)
                    request.future.set_result(result)
                except Exception as e:
                    # RETRY LOGIC
                    if request.attempts < len(self._retry_delays):
                        delay = self._retry_delays[request.attempts]
                        logger.warning("api_request_retry", attempt=request.attempts+1, delay=delay, error=str(e))
                        
                        # Create a new request with incremented attempts
                        retry_request = request._replace(attempts=request.attempts + 1)
                        
                        # Schedule retry after delay
                        asyncio.create_task(self._schedule_retry(priority, retry_request, delay))
                        
                        # We don't set the future result yet, we wait for the retry to finish
                        # To do this correctly, we'd need a more complex future management.
                        # For now, the original future will be resolved by the final attempt.
                        # But we need to pass the original future to the retry_request.
                        # Actually, the retry_request should have the same future.
                    else:
                        logger.error("api_request_failed_after_retries", error=str(e))
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
        await self._queue.put((priority, request))

    async def process_next(self):
        pass

    async def stop(self):
        self._running = False
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
