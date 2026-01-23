"""Add tasks file_id and chat_id

Revision ID: 6a8f0c3d9b12
Revises: 2d9f6b8c1a0d
Create Date: 2026-01-23 14:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6a8f0c3d9b12"
down_revision: Union[str, Sequence[str], None] = "2d9f6b8c1a0d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("tasks", sa.Column("chat_id", sa.BigInteger(), nullable=True))
    op.add_column("tasks", sa.Column("file_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_tasks_chat_id"), "tasks", ["chat_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tasks_chat_id"), table_name="tasks")
    op.drop_column("tasks", "file_id")
    op.drop_column("tasks", "chat_id")
