"""Initialize alembic tracking with the current schema baseline.

The test-stand DB was created via create_all + raw SQL migrations
(migrations/001-003). This revision is a no-op baseline so that
`alembic upgrade head` starts from the current state.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Baseline: tables are created by the historical create_all + SQL migrations.
    # Stamp only — nothing to do here for databases that already exist.
    pass


def downgrade() -> None:
    pass