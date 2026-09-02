"""Alembic runtime helpers.

Usage (TS #76):
    python -m app.db.migrations upgrade      # apply all pending migrations
    python -m app.db.migrations stamp head   # mark current DB as up-to-date
"""
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    return cfg


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    cfg = _config()
    if cmd == "upgrade":
        command.upgrade(cfg, args[0] if args else "head")
    elif cmd == "stamp":
        command.stamp(cfg, args[0] if args else "head")
    elif cmd == "downgrade":
        command.downgrade(cfg, args[0] if args else "-1")
    elif cmd == "revision":
        command.revision(cfg, message=args[0] if args else None, autogenerate=True)
    elif cmd == "current":
        command.current(cfg)
    elif cmd == "history":
        command.history(cfg)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()