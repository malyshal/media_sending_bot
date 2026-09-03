"""Ensure posts.id is VARCHAR (defensive for drifted databases).

The ORM declares Post.id as String; on databases created by an older
version of the code the column may still be BIGINT, which breaks
session.get(Post, "UG9zdD...") with:

    operator does not exist: bigint = character varying

Revision ID: 0008_posts_id_varchar
Revises: 0007_posts_tags_jsonb
Create Date: 2026-09-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_posts_id_varchar"
down_revision: Union[str, None] = "0007_posts_tags_jsonb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'posts'
                  AND column_name = 'id'
                  AND udt_name IN ('int8', 'int4')
            ) THEN
                ALTER TABLE posts
                    ALTER COLUMN id TYPE varchar USING id::varchar;
            END IF;

            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'post_history'
                  AND column_name = 'post_id'
                  AND udt_name IN ('int8', 'int4')
            ) THEN
                ALTER TABLE post_history
                    ALTER COLUMN post_id TYPE varchar USING post_id::varchar;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    pass
