"""Apply pending SQL migrations from migrations/*.sql to the database.

Usage:
    python -m scripts.run_migrations            # uses DATABASE_URL from .env
    python -m scripts.run_migrations --url postgresql+asyncpg://...  # override URL

Idempotent-ish: each migration file should be written to be safe if possible
(e.g. use IF EXISTS / DO blocks). This runner tracks applied files so it does
not re-apply them.
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
APPLIED_TABLE = "schema_migrations"


def _to_asyncpg_dsn(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return url


async def run(database_url: str) -> None:
    conn = await asyncpg.connect(_to_asyncpg_dsn(database_url))
    try:
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {APPLIED_TABLE} ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row["name"]
            for row in await conn.fetch(f"SELECT name FROM {APPLIED_TABLE}")
        }
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                print(f"skip {path.name} (already applied)")
                continue
            sql = path.read_text(encoding="utf-8")
            print(f"applying {path.name} ...")
            try:
                await conn.execute(sql)
            except asyncpg.PostgresError as e:
                # Allow already-applied non-idempotent statements to fail softly
                msg = str(e)
                if "already exists" in msg or "42710" in msg or "42701" in msg or "duplicate" in msg.lower():
                    print(f"  (already applied, ignoring: {msg.splitlines()[0]})")
                else:
                    raise
            await conn.execute(
                f"INSERT INTO {APPLIED_TABLE} (name) VALUES ($1) ON CONFLICT DO NOTHING",
                path.name,
            )
            print(f"ok {path.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    url = None
    args = sys.argv[1:]
    if "--url" in args:
        url = args[args.index("--url") + 1]
    if not url:
        from app.core.config import settings

        url = settings.database_url
    asyncio.run(run(url))