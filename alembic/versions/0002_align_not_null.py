"""Align chat_configs nullability with the ORM (TS #75).

Revision ID: 0002_align_not_null
Revises: 0001_baseline
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002_align_not_null"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The legacy raw-SQL table creation left some columns nullable;
    # the ORM declares them NOT NULL (with ORM-side defaults).
    # Backfill NULLs so the constraint can be applied safely.
    op.execute("UPDATE chat_configs SET include_tags = '[]'::jsonb WHERE include_tags IS NULL")
    op.execute("UPDATE chat_configs SET exclude_tags = '[]'::jsonb WHERE exclude_tags IS NULL")
    op.execute("UPDATE chat_configs SET updated_at = now() WHERE updated_at IS NULL")
    op.alter_column('chat_configs', 'include_tags', existing_type=postgresql.JSONB(), nullable=False)
    op.alter_column('chat_configs', 'exclude_tags', existing_type=postgresql.JSONB(), nullable=False)
    op.alter_column('chat_configs', 'updated_at', existing_type=sa.DateTime(), nullable=False)


def downgrade() -> None:
    op.alter_column('chat_configs', 'updated_at', existing_type=sa.DateTime(), nullable=True)
    op.alter_column('chat_configs', 'exclude_tags', existing_type=postgresql.JSONB(), nullable=True)
    op.alter_column('chat_configs', 'include_tags', existing_type=postgresql.JSONB(), nullable=True)
