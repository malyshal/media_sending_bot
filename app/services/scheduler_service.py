"""Scheduled delivery (TS #36-43).

Schedule format is strict "HH:MM" (chat-local time) + schedule_mode:
  daily          -> every day at HH:MM
  weekly         -> on weekday schedule_interval (0=Mon .. 6=Sun) at HH:MM
  every_n_days   -> every N days at HH:MM (counted from last_batch_time)
  hourly         -> every hour at minute MM
  every_n_hours  -> every N hours at minute MM
"""
from typing import List
import structlog
from datetime import datetime, timedelta
import zoneinfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.chat import ChatConfig
from app.services.delivery_service import DeliveryService
from app.db.repositories.chat_repository import ChatRepository

logger = structlog.get_logger()

VALID_MODES = ("daily", "hourly", "every_n_days", "every_n_hours", "weekly")


class SchedulerService:
    def __init__(self, bot, delivery_service: DeliveryService, chat_repo: ChatRepository):
        self.bot = bot
        self.delivery_service = delivery_service
        self.chat_repo = chat_repo

    async def check_and_send_scheduled(self, session: AsyncSession):
        """Checks all active chats once per minute (TS #39)."""
        query = select(ChatConfig).where(ChatConfig.auto_send == True)
        result = await session.execute(query)
        chats = result.scalars().all()

        now_utc = datetime.now(zoneinfo.ZoneInfo("UTC"))

        for config in chats:
            if not config.schedule:
                continue
            try:
                due, due_key = self._is_due(config, now_utc)
                if not due:
                    continue
                logger.info("scheduled_send_triggered", chat_id=config.chat_id)

                sent_count = await self.delivery_service.send_batch_posts(
                    chat_id=config.chat_id,
                    include_tags=config.include_tags,
                    exclude_tags=config.exclude_tags,
                    max_posts=config.schedule_max_posts,
                    show_links=config.show_post_links,
                )

                # TS #41: timestamp only after at least one delivered post.
                # TS #103/#85: if no posts -> no timestamp update, so the batch
                # is retried on the next loop once posts appear.
                if sent_count > 0:
                    config.last_batch_time = now_utc.replace(tzinfo=None)
                    await session.commit()
                    logger.info("scheduled_send_completed", chat_id=config.chat_id, sent_count=sent_count)
                else:
                    logger.info("scheduled_send_no_posts", chat_id=config.chat_id)
            except Exception as e:
                logger.error("schedule_check_error", chat_id=config.chat_id, error=str(e))

    # ------------------------------------------------------------ logic

    def _parse_hhmm(self, schedule: str) -> tuple[int, int] | None:
        parts = schedule.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return None
        return int(parts[0]), int(parts[1])

    def _is_due(self, config: ChatConfig, now_utc: datetime) -> tuple[bool, str]:
        """Returns (is_due, dedup_key) — key guards against double-run (TS #40)."""
        schedule = config.schedule or ""
        mode = config.schedule_mode or "daily"
        interval = max(1, config.schedule_interval or 1)
        if mode in ("hourly", "every_n_hours"):
            # schedule = "*:MM" — only the minute matters
            parts = schedule.split(":")
            if len(parts) != 2 or not parts[1].isdigit():
                logger.error("invalid_schedule_format", chat_id=config.chat_id, schedule=schedule)
                return False, ""
            target_h, target_m = None, int(parts[1])
        else:
            parsed = self._parse_hhmm(schedule)
            if parsed is None:
                logger.error("invalid_schedule_format", chat_id=config.chat_id, schedule=schedule)
                return False, ""
            target_h, target_m = parsed

        try:
            tz = zoneinfo.ZoneInfo(config.timezone or "UTC")
        except Exception:
            tz = zoneinfo.ZoneInfo("UTC")
        local_now = now_utc.astimezone(tz)

        # minute match for all modes (hourly modes use only the minute part)
        if mode not in ("hourly", "every_n_hours") and local_now.minute != target_m:
            return False, ""

        last = config.last_batch_time
        last_local = last.replace(tzinfo=zoneinfo.ZoneInfo("UTC")).astimezone(tz) if last else None

        if mode == "daily":
            if local_now.hour != target_h:
                return False, ""
            if last_local and last_local.date() >= local_now.date():
                return False, ""
            return True, f"daily:{local_now.date().isoformat()}"

        if mode == "weekly":
            if local_now.hour != target_h:
                return False, ""
            if local_now.weekday() != (config.schedule_interval or 0):
                return False, ""
            if last_local and (local_now.date() - last_local.date()).days < 7:
                return False, ""
            return True, f"weekly:{local_now.date().isocalendar()[1]}"

        if mode == "every_n_days":
            if local_now.hour != target_h:
                return False, ""
            if last_local and (local_now.date() - last_local.date()).days < interval:
                return False, ""
            return True, f"n_days:{interval}"

        if mode == "hourly":
            if local_now.minute != target_m:
                return False, ""
            if last_local and (now_utc.replace(tzinfo=None) - last.replace(tzinfo=None)) < timedelta(hours=1):
                return False, ""
            return True, f"hourly:{local_now.date().isoformat()}:{local_now.hour}"

        if mode == "every_n_hours":
            if local_now.minute != target_m:
                return False, ""
            if last_local and (now_utc.replace(tzinfo=None) - last.replace(tzinfo=None)) < timedelta(hours=interval):
                return False, ""
            return True, f"n_hours:{interval}:{local_now.date().isoformat()}:{local_now.hour}"

        logger.error("invalid_schedule_mode", chat_id=config.chat_id, mode=mode)
        return False, ""
