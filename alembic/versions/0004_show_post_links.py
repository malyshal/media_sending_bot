"""Add show_post_links setting (TS UX: source post link in caption).

Revision ID: 0003b_show_post_links
Revises: 0003_drop_schema_migrations
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003b_show_post_links"
down_revision: Union[str, None] = "0003_drop_schema_migrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_configs', sa.Column('show_post_links', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('chat_configs', 'show_post_links')
