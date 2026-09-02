# JoyBot — bot de Telegram para entregar publicaciones de JoyReactor

JoyBot es un bot asíncrono de Telegram que obtiene publicaciones de [joyreactor.cc](https://joyreactor.cc), las filtra por tus etiquetas y las entrega en Telegram — bajo demanda o automáticamente según un horario.

**Idiomas:** [Русский](README.md) (principal) · [English](README.en.md) · Español

---

## Características

- 🔍 **Búsqueda de etiquetas** — menú interactivo, búsqueda local en caché + autocompletado vía API de JoyReactor;
- 📥 **Etiquetas Include / 🚫 Exclude** — control fino del feed (`(include OR …) AND NOT (exclude OR …)`);
- ▶️ **`/next`** — recibir publicaciones bajo demanda (límite configurable);
- ⏰ **Horario** — envío automático diario a una hora fija con zona horaria por chat y botón de encendido/apagado;
- 🛡 **Sin duplicados** — reserva atómica de publicaciones en PostgreSQL (`INSERT … ON CONFLICT DO NOTHING`);
- 🖼 **Multimedia** — fotos, GIFs, video: descarga, conversión GIF→MP4 con ffmpeg, envío de álbumes (texto + varios medios);
- 🧹 **Limpieza automática** — TTL de caché, rotación del historial, procedimiento de eliminación de datos (GDPR);
- 📊 **Observabilidad** — `/stats` con el estado del sistema y contadores de eventos;
- 🚫 **Anti-spam** — historial de envíos por chat, protección contra condiciones de carrera.

## Comandos

El menú está disponible con `/` o el botón **Menú** junto al campo de entrada. Todos los ajustes también están disponibles mediante botones inline.

| Comando | Descripción |
|---|---|
| `/start` | Menú principal (y onboarding en el primer arranque) |
| `/next` | Obtener publicaciones según tus etiquetas |
| `/settings` | Ajustes |
| `/stop` | Desactivar el envío automático |
| `/help` | Ayuda |
| `/delete_my_data` | Solicitar eliminación de datos (30 días de recuperación) |
| `/restore` | Restaurar cuenta |
| `/search_tags <consulta>` | Buscar etiquetas |

Administrativas: `/stats`, `/force_send <chat_id>`.

## Inicio rápido (Docker — para pruebas)

```bash
cp .env.example .env    # rellena BOT_TOKEN y las URL de BD/Redis
docker compose up -d
docker compose logs -f bot
```

## Producción (LXC, systemd — escenario principal)

```bash
# 1. Usuario y directorio
sudo useradd -r -s /usr/sbin/nologin joybot
sudo mkdir -p /opt/joybot

# 2. Código y dependencias
sudo cp -r . /opt/joybot
cd /opt/joybot
sudo python3 -m venv venv
sudo ./venv/bin/pip install .

# 3. Configuración
sudo cp .env.example .env && sudo nano .env
# DATABASE_URL y REDIS_URL deben apuntar a los hosts reales de PostgreSQL/Redis

# 4. Esquema de BD: aplicar migraciones de Alembic
sudo ./venv/bin/python -m app.db.migrations upgrade

# 5. Servicio
sudo cp deploy/joybot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now joybot
sudo journalctl -u joybot -f
```

## Configuración (`.env`)

| Variable | Valor por defecto | Descripción |
|---|---|---|
| `BOT_TOKEN` | — | Token del bot de [@BotFather](https://t.me/BotFather) |
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host:5432/db` |
| `REDIS_URL` | — | `redis://host:6379/0` |
| `JOYREACTOR_BASE_URL` | `https://joyreactor.cc` | Sitio de origen |
| `JOYREACTOR_API_URL` | `https://api.joyreactor.com/graphql` | API GraphQL |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `CACHE_RETENTION_HOURS` | `6` | TTL de la caché de publicaciones |
| `MAX_FRESH_POSTS_FOR_BATCH` | `20` | Tamaño de selección de la caché |
| `INITIAL_ADMIN_IDS` | `[]` | IDs de Telegram de los administradores |
| `DEFAULT_TIMEZONE` | `Europe/Minsk` | Zona horaria predeterminada |
| `API_REQUEST_INTERVAL` | `2.5` | Pausa entre llamadas a la API, segundos |
| `MAX_MEDIA_SIZE_MB` | `50` | Límite de tamaño de multimedia |
| `QUEUE_TYPE` | `memory` | Cola: `memory` (un solo proceso); `redis` reservado |

## Migraciones de la base de datos

El esquema se gestiona con **Alembic** (los cambios solo mediante migraciones, según TS #76):

```bash
python -m app.db.migrations upgrade     # aplicar migraciones pendientes
python -m app.db.migrations current     # revisión actual
python -m app.db.migrations revision "descripción"   # crear migración (autogenerate)
```

## Arquitectura

```
Telegram → Aiogram Handlers → Services → PostgreSQL
                     │              │
                     │              ├─ PostService → JoyReactorClient → APIQueue → JoyReactor API
                     │              ├─ DeliveryService → MediaManager
                     │              └─ SchedulerService → DeliveryService
                     │
                     └─ Redis (FSM)
```

- **Handlers** — actualizaciones de Telegram, validación, UI;
- **Services** — lógica de negocio; **Repositories** — acceso a la base de datos;
- **APIQueue** — cola única hacia JoyReactor: rate limit, prioridades, reintentos;
- **MediaManager** — descarga, procesamiento (ffmpeg/Pillow), limpieza de temporales.

## Requisitos

- Python 3.11+
- PostgreSQL 14+
- Redis
- ffmpeg (para conversión de GIF/video)

## Pruebas

```bash
pip install pytest pytest-asyncio
pytest tests/
```

## Licencia

MIT — ver [LICENSE](LICENSE).