"""Ensure posts.tags is JSONB (defensive migration for drifted databases).

The ORM declares Post.tags as JSONB; on databases created before the
jsonb migration (or restored from an old dump), the column may still be
plain `json`, which breaks PostRepository tag filtering:

    operator does not exist: json @> jsonb

Revision ID: 0007_posts_tags_jsonb
Revises: 0006_schedule_modes
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_posts_tags_jsonb"
down_revision: Union[str, None] = "0006_schedule_modes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'posts'
                  AND column_name = 'tags'
                  AND udt_name = 'json'
            ) THEN
                ALTER TABLE posts
                    ALTER COLUMN tags TYPE jsonb USING tags::jsonb;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    pass  # converting back to json loses nothing but is unnecessary
