"""Add users.seconds column

Revision ID: 9b0f8b2e4d1a
Revises: 7d2e6d0c4f31
Create Date: 2026-01-23 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b0f8b2e4d1a"
down_revision: Union[str, Sequence[str], None] = "7d2e6d0c4f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "seconds")
