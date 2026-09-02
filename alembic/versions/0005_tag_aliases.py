"""Tag aliases: canonical tag resolution table.

Revision ID: 0005_tag_aliases
Revises: 0003b_show_post_links
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_tag_aliases"
down_revision: Union[str, None] = "0003b_show_post_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tag_aliases',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('query', sa.String(), nullable=False),
        sa.Column('canonical', sa.String(), nullable=True),
        sa.Column('resolved', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('tag_aliases_pkey')),
        sa.UniqueConstraint('query', name='uq_tag_aliases_query'),
    )
    op.create_index('ix_tag_aliases_resolved', 'tag_aliases', ['resolved'])


def downgrade() -> None:
    op.drop_index('ix_tag_aliases_resolved', table_name='tag_aliases')
    op.drop_table('tag_aliases')
