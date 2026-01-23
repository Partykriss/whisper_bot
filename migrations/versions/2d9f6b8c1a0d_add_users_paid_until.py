"""Add users.paid_until column

Revision ID: 2d9f6b8c1a0d
Revises: c6d7b3e8a5f0
Create Date: 2026-01-23 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "2d9f6b8c1a0d"
down_revision: Union[str, Sequence[str], None] = "c6d7b3e8a5f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("paid_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "paid_until")
