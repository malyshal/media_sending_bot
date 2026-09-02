"""Lightweight in-process counters for observability (TS #96).

Counts key events since application start. Single-process scope is fine
for the current deployment; a Redis backend can be added later if the bot
is scaled to multiple instances (TS #66).
"""
import threading
import structlog

logger = structlog.get_logger()

_EVENTS = (
    "api_requests",        # JoyReactor API requests executed
    "api_retries",         # API request retries
    "api_failures",        # API requests failed after retries
    "posts_received",      # posts fetched from JoyReactor
    "posts_sent",          # media groups/messages delivered to Telegram
    "delivery_failures",   # failed deliveries
)


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {e: 0 for e in _EVENTS}

    def inc(self, event: str, amount: int = 1):
        if event not in self._counters:
            return
        with self._lock:
            self._counters[event] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def render(self) -> str:
        s = self.snapshot()
        return (
            "📈 *Счётчики (с момента старта)*\n"
            f"🔄 Запросов к API: {s['api_requests']}\n"
            f"♻️ Ретраев API: {s['api_retries']}\n"
            f"❌ Ошибок API: {s['api_failures']}\n"
            f"📥 Публикаций получено: {s['posts_received']}\n"
            f"📤 Публикаций отправлено: {s['posts_sent']}\n"
            f"⚠️ Ошибок доставки: {s['delivery_failures']}"
        )


# Global singleton
metrics = Metrics()