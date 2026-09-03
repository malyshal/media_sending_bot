"""Schedule modes: daily / hourly / every_n_days / every_n_hours / weekly.

Revision ID: 0006_schedule_modes
Revises: 0005_tag_aliases
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_schedule_modes"
down_revision: Union[str, None] = "0005_tag_aliases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_configs', sa.Column('schedule_mode', sa.String(), nullable=False, server_default='daily'))
    op.add_column('chat_configs', sa.Column('schedule_interval', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    op.drop_column('chat_configs', 'schedule_interval')
    op.drop_column('chat_configs', 'schedule_mode')
