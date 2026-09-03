# JoyBot — Telegram-бот доставки постов с JoyReactor

JoyBot — асинхронный Telegram-бот, который получает публикации с [joyreactor.cc](https://joyreactor.cc), фильтрует их по вашим тегам и доставляет в Telegram — вручную по команде или автоматически по расписанию.

**Языки:** [Русский](README.md) (основной) · [English](README.en.md) · [Español](README.es.md)

---

## Возможности

- 🔍 **Поиск по тегам** — интерактивное меню, локальный поиск в кэше + автодополнение через API JoyReactor;
- 📥 **Include / 🚫 Exclude теги** — тонкая настройка ленты (логика `(include OR …) AND NOT (exclude OR …)`);
- ▶️ **`/next`** — получить посты вручную (лимит настраивается);
- ⏰ **Расписание** — гибкие режимы: каждый день, раз в неделю, раз в N дней, каждый час, раз в N часов — с часовым поясом чата и кнопкой Вкл/Выкл;
- 🛡 **Защита от дублей** — атомарное резервирование постов в PostgreSQL (`INSERT … ON CONFLICT DO NOTHING`);
- 🖼 **Медиа** — фото, GIF, видео: скачивание, конвертация GIF→MP4 через ffmpeg, отправка альбомов (текст + несколько медиа); медиа кэшируются на диск (`MEDIA_DIR`) и префетчатся в фоне — доставка мгновенная;
- 🏷 **Канонизация тегов** — пользовательские запросы автоматически сводятся к каноничным тегам API в фоне;
- 🧹 **Автоочистка** — TTL кэша, ротация истории, GDPR-процедура удаления данных;
- 📊 **Наблюдаемость** — `/stats` с состоянием системы и счётчиками событий;
- 🚫 **Без спама** — история отправленных постов на каждый чат, race condition protection.

## Команды

Меню доступно при нажатии `/` или кнопки **Меню** рядом с полем ввода. Все настройки также доступны через inline-кнопки.

| Команда | Описание |
|---|---|
| `/start` | Главное меню (и онбординг при первом запуске) |
| `/next` | Получить посты по вашим тегам |
| `/settings` | Настройки |
| `/stop` | Выключить автоотправку |
| `/help` | Справка |
| `/delete_my_data` | Запросить удаление данных (30 дней на восстановление) |
| `/restore` | Восстановить аккаунт |
| `/search_tags <запрос>` | Поиск тегов |

Административные: `/stats`, `/force_send <chat_id>`.

## Быстрый старт (Docker — для тестирования)

```bash
cp .env.example .env    # заполните BOT_TOKEN и адреса БД/Redis
docker compose up -d
docker compose logs -f bot
```

## Production (LXC, systemd — основной сценарий)

```bash
# 1. Пользователь и каталог
sudo useradd -r -s /usr/sbin/nologin joybot
sudo mkdir -p /opt/joybot

# 2. Код и зависимости
sudo cp -r . /opt/joybot
cd /opt/joybot
sudo python3 -m venv venv
sudo ./venv/bin/pip install .

# 3. Конфигурация
sudo cp .env.example .env && sudo nano .env
# DATABASE_URL и REDIS_URL должны указывать на реальные хосты PostgreSQL/Redis

# 4. Схема БД: применить миграции Alembic
sudo ./venv/bin/python -m app.db.migrations upgrade

# 5. Сервис
sudo cp deploy/joybot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now joybot
sudo journalctl -u joybot -f
```

## Конфигурация (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | Токен бота от [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | — | `redis://host:6379/0` |
| `JOYREACTOR_BASE_URL` | `https://joyreactor.cc` | Сайт-источник |
| `JOYREACTOR_API_URL` | `https://api.joyreactor.com/graphql` | GraphQL API |
| `LOG_LEVEL` | `INFO` | Уровень логирования |
| `CACHE_RETENTION_HOURS` | `6` | TTL кэша публикаций |
| `MAX_FRESH_POSTS_FOR_BATCH` | `20` | Размер выборки из кэша |
| `INITIAL_ADMIN_IDS` | `[]` | Telegram ID администраторов |
| `DEFAULT_TIMEZONE` | `Europe/Minsk` | Часовой пояс по умолчанию |
| `API_REQUEST_INTERVAL` | `2.5` | Пауза между запросами к API, сек |
| `MAX_MEDIA_SIZE_MB` | `50` | Лимит размера медиа |
| `QUEUE_TYPE` | `memory` | Очередь: `memory` (один процесс); `redis` зарезервирован |
| `MEDIA_DIR` | `tmp/media` | Папка кэша медиа (можно смонтировать внешний диск) |

## Миграции БД

Схема управляется **Alembic** (изменения только через миграции, TS #76):

```bash
python -m app.db.migrations upgrade     # применить все миграции
python -m app.db.migrations current     # текущая ревизия
python -m app.db.migrations revision "описание"   # создать миграцию (autogenerate)
```

## Архитектура

```
Telegram → Aiogram Handlers → Services → PostgreSQL
                     │              │
                     │              ├─ PostService → JoyReactorClient → APIQueue → JoyReactor API
                     │              ├─ DeliveryService → MediaManager
                     │              └─ SchedulerService → DeliveryService
                     │
                     └─ Redis (FSM)
```

- **Handlers** — Telegram-обновления, валидация, UI;
- **Services** — бизнес-логика; **Repositories** — доступ к БД;
- **APIQueue** — единая очередь к JoyReactor: rate limit, приоритеты, retry;
- **MediaManager** — скачивание, обработка (ffmpeg/Pillow), очистка временных файлов.

## Требования

- Python 3.11+
- PostgreSQL 14+
- Redis
- ffmpeg (для конвертации GIF/видео)

## Известные ограничения

- **Индексация JoyReactor**: API сайта обновляется с задержкой (от минут до нескольких часов). Совершенно новые посты и теги могут появиться в боте позже, чем на сайте — подождите и попробуйте снова.
- Теги добавляются как есть; в фоновом режиме бот сводит их к каноничным именам API (например, «тюлень» → `sea calf`).

## Тестирование

```bash
pip install pytest pytest-asyncio
pytest tests/
```

## Лицензия

MIT — см. [LICENSE](LICENSE).