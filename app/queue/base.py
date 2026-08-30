from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable

class BaseQueue(ABC):
    @abstractmethod
    async def enqueue(self, task: Callable[..., Awaitable[Any]], *args, priority: int = 1, **kwargs):
        pass

    @abstractmethod
    async def process_next(self):
        pass
