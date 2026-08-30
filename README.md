# JoyBot 🤖

Welcome to JoyBot — a professional Telegram bot that delivers content from joyreactor.cc based on user preferences and individual schedules.

[Русская версия 🇷🇺](./README.ru.md)

## ✨ Features

- **Smart Content Delivery**: Get the latest posts via `/next` or automatically according to your schedule.
- **Advanced Filtering**: Custom "Include" and "Exclude" tag lists.
- **Individual Scheduling**: Set your own delivery times and days, with full timezone support.
- **Rate-Limit Protected**: A centralized API queue ensures the bot respects JoyReactor's limits.
- **Production-Ready**: Distributed locking for post delivery, automatic cache cleanup, and structured logging.
- **Privacy Focused**: Full data deletion with a 30-day recovery period.

## 🚀 Quick Start

### Environment Variables
Create a `.env` file based on `.env.example`:
- `BOT_TOKEN`: Your Telegram Bot token.
- `DATABASE_URL`: PostgreSQL connection string.
- `REDIS_URL`: Redis connection string (for queues/locks).
- `API_REQUEST_INTERVAL`: Min interval between API requests (default: 2.0).

### Installation
Using Docker (recommended):
```bash
docker-compose up -d
```

Or manual installation:
```bash
pip install -r requirements.txt
python main.py
```

## 🛠 Commands

- `/next` — Get the next matching post.
- `/settings` — Manage tags, schedule, and auto-send toggle.
- `/search_tags <query>` — Search for canonical tags on JoyReactor.
- `/stop` — Disable automatic deliveries for the current chat.
- `/delete_my_data` — Request permanent data deletion.
- `/restore` — Cancel a pending deletion request.
- `/stats` — (Admin only) System statistics.
- `/force_send` — (Admin only) Force a post delivery to the current chat.

## 🏗 Architecture

- **Language**: Python 3.11+ (asyncio)
- **Framework**: aiogram 3.x
- **Database**: PostgreSQL 14+ (SQLAlchemy Async)
- **Queue/Caching**: Redis / Asyncio PriorityQueue
- **Media**: Pillow, FFmpeg (for optimization)
