"""Drop legacy schema_migrations table (replaced by alembic_version).

Revision ID: 0003_drop_schema_migrations
Revises: 0002_align_not_null
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_drop_schema_migrations"
down_revision: Union[str, None] = "0002_align_not_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The old raw-SQL runner (scripts/run_migrations.py) tracking table;
    # superseded by Alembic (TS #76).
    op.execute("DROP TABLE IF EXISTS schema_migrations")


def downgrade() -> None:
    # No-op: legacy runner is deprecated.
    pass
