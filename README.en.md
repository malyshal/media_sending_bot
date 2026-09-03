# JoyBot — JoyReactor delivery Telegram bot

JoyBot is an async Telegram bot that fetches posts from [joyreactor.cc](https://joyreactor.cc), filters them by your tags and delivers them to Telegram — on demand or automatically on a schedule.

**Languages:** [Русский](README.md) (primary) · English · [Español](README.es.md)

---

## Features

- 🔍 **Tag search** — interactive menu, local cache search + JoyReactor API autocomplete;
- 📥 **Include / 🚫 Exclude tags** — fine-grained feed control (`(include OR …) AND NOT (exclude OR …)`);
- ▶️ **`/next`** — fetch posts on demand (configurable limit);
- ⏰ **Schedule** — flexible modes: daily, weekly, every N days, hourly, every N hours — with per-chat timezone and an on/off toggle;
- 🛡 **No duplicates** — atomic post reservation in PostgreSQL (`INSERT … ON CONFLICT DO NOTHING`);
- 🖼 **Media** — photos, GIFs, video: download, GIF→MP4 conversion via ffmpeg, album delivery (text + multiple media); media is cached on disk (`MEDIA_DIR`) and prefetched in the background — instant delivery;
- 🏷 **Tag canonicalization** — user queries are automatically mapped to canonical API tag names in the background;
- 🧹 **Auto-cleanup** — cache TTL, history rotation, GDPR-style data deletion procedure;
- 📊 **Observability** — `/stats` with system state and event counters;
- 🚫 **Anti-spam** — per-chat delivery history, race condition protection.

## Commands

The menu is available via `/` or the **Menu** button next to the input field. All settings are also reachable through inline buttons.

| Command | Description |
|---|---|
| `/start` | Main menu (and onboarding on first run) |
| `/next` | Get posts matching your tags |
| `/settings` | Settings |
| `/stop` | Disable automatic delivery |
| `/help` | Help |
| `/delete_my_data` | Request data deletion (30-day recovery period) |
| `/restore` | Restore account |
| `/search_tags <query>` | Search tags |

Administrative: `/stats`, `/force_send <chat_id>`.

## Quick start (Docker — for testing)

```bash
cp .env.example .env    # fill in BOT_TOKEN and DB/Redis URLs
docker compose up -d
docker compose logs -f bot
```

## Production (LXC, systemd — primary scenario)

```bash
# 1. User and directory
sudo useradd -r -s /usr/sbin/nologin joybot
sudo mkdir -p /opt/joybot

# 2. Code and dependencies
sudo cp -r . /opt/joybot
cd /opt/joybot
sudo python3 -m venv venv
sudo ./venv/bin/pip install .

# 3. Configuration
sudo cp .env.example .env && sudo nano .env
# DATABASE_URL and REDIS_URL must point to the real PostgreSQL/Redis hosts

# 4. DB schema: apply Alembic migrations
sudo ./venv/bin/python -m app.db.migrations upgrade

# 5. Service
sudo cp deploy/joybot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now joybot
sudo journalctl -u joybot -f
```

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | — | `redis://host:6379/0` |
| `JOYREACTOR_BASE_URL` | `https://joyreactor.cc` | Source site |
| `JOYREACTOR_API_URL` | `https://api.joyreactor.com/graphql` | GraphQL API |
| `LOG_LEVEL` | `INFO` | Logging level |
| `CACHE_RETENTION_HOURS` | `6` | Post cache TTL |
| `MAX_FRESH_POSTS_FOR_BATCH` | `20` | Cache selection size |
| `INITIAL_ADMIN_IDS` | `[]` | Admin Telegram IDs |
| `DEFAULT_TIMEZONE` | `Europe/Minsk` | Default timezone |
| `API_REQUEST_INTERVAL` | `2.5` | Delay between API calls, seconds |
| `MAX_MEDIA_SIZE_MB` | `50` | Media size limit |
| `QUEUE_TYPE` | `memory` | Queue: `memory` (single process); `redis` reserved |
| `MEDIA_DIR` | `tmp/media` | Media cache directory (mount an external disk) |

## Database migrations

The schema is managed by **Alembic** (changes only via migrations, per TS #76):

```bash
python -m app.db.migrations upgrade     # apply all pending migrations
python -m app.db.migrations current     # current revision
python -m app.db.migrations revision "description"   # create a migration (autogenerate)
```

## Architecture

```
Telegram → Aiogram Handlers → Services → PostgreSQL
                     │              │
                     │              ├─ PostService → JoyReactorClient → APIQueue → JoyReactor API
                     │              ├─ DeliveryService → MediaManager
                     │              └─ SchedulerService → DeliveryService
                     │
                     └─ Redis (FSM)
```

- **Handlers** — Telegram updates, validation, UI;
- **Services** — business logic; **Repositories** — database access;
- **APIQueue** — single queue to JoyReactor: rate limit, priorities, retry;
- **MediaManager** — download, processing (ffmpeg/Pillow), temp file cleanup.

## Requirements

- Python 3.11+
- PostgreSQL 14+
- Redis
- ffmpeg (for GIF/video conversion)

## Known limitations

- **JoyReactor indexing**: the site API updates with a delay (from minutes to a few hours). Brand-new posts and tags may appear in the bot later than on the website — wait and try again.
- Tags are added as entered; in the background the bot maps them to canonical API tag names (e.g. "тюлень" → `sea calf`).

## Testing

```bash
pip install pytest pytest-asyncio
pytest tests/
```

## License

MIT — see [LICENSE](LICENSE).